/*
 * Copyright (c) 2026 Prajnaana Technologies
 * SPDX-License-Identifier: MIT
 *
 * Part of the Multi-Core RPC Framework.
 * See the LICENSE file in the project root for the full license text.
 *
 * Original Author: Mamatha BV
 */

#include "osal.h"
#include<errno.h>
int connect_count = 0;

/* Network MEMBERSHIP: g_core_present[c]==1 means core c is currently part of the
 * RPC network (connected to the server). The SERVER is the authority - it sets
 * these on accept/disconnect and BROADCASTS the bitmap to every client, so all
 * cores show the same function menu. A CLIENT only sets the server bit locally
 * and otherwise adopts whatever bitmap the server sends. Own core is always
 * present (see rpc_is_core_present). */
int g_core_present[RPCCORE_COUNT] = { 0 };

/* Set to 1 when a CLIENT exhausts its connect retries and cannot reach the
 * server. main()/the JNI entry checks this to fail gracefully instead of
 * hanging - socket helpers NEVER call exit() now (which on Android would kill
 * the whole app process). */
volatile int g_rpc_link_failed = 0;

StructSocketInfo sSocketInfo[RPCCORE_COUNT];

/* SINGLE-PORT hub. The server (hub) listens on ONE shared port (rpc_config_port(),
 * default PORT=8848) for ALL clients, instead of a distinct port per client. A
 * single listen socket can accept any number of connections; each accepted
 * connection is a separate data socket. Because the port no longer identifies
 * which core connected, every client announces its core id in a tiny handshake
 * (see io_handshake_*), which the hub reads right after accept() to place the
 * socket in the correct sSocketInfo[] slot. */
static os_socket_t g_listen_fd = OS_INVALID_SOCKET;   /* shared listen socket (server only) */

/* Send/recv EXACTLY n bytes over a raw socket (used only for the small
 * connection handshake, before the marshalling path is active). Returns 0 on
 * success, -1 on error or peer-close. Loops to tolerate short reads/writes. */
static int io_write_all(os_socket_t fd, const void *buf, int n)
{
	const char *p   = (const char *)buf;
	int         off = 0;
	while (off < n)
	{
		int w = os_write(fd, p + off, n - off);
		if (w <= 0) return -1;
		off += w;
	}
	return 0;
}

static int io_read_all(os_socket_t fd, void *buf, int n)
{
	char *p   = (char *)buf;
	int   off = 0;
	while (off < n)
	{
		int r = os_read(fd, p + off, n - off);
		if (r <= 0) return -1;
		off += r;
	}
	return 0;
}

/* Handshake: the client sends its own core id as a 4-byte network-order int
 * immediately after connect(); the hub reads it immediately after accept(). */
static int io_handshake_send(os_socket_t fd, enumFnCore eSelfCore)
{
	unsigned int net = htonl((unsigned int)eSelfCore);
	return io_write_all(fd, &net, (int)sizeof(net));
}

static int io_handshake_recv(os_socket_t fd, enumFnCore *peCore)
{
	unsigned int net = 0;
	if (0 != io_read_all(fd, &net, (int)sizeof(net))) return -1;
	*peCore = (enumFnCore)ntohl(net);
	if ((int)*peCore < 0 || *peCore >= RPCCORE_COUNT) return -1;   /* reject garbage */
	return 0;
}

int rpc_is_core_present(enumFnCore c)
{
	if (c == get_this_core())  return 1;   /* self is always present */
	if (c < RPCCORE_COUNT)     return g_core_present[c];
	return 0;
}

/* CLIENT: adopt the membership bitmap just received from the server. */
void rpc_apply_membership(const unsigned char *flags)
{
	int i;
	for (i = 0; i < RPCCORE_COUNT; i++)
	{
		if (i != get_this_core()) g_core_present[i] = flags[i] ? 1 : 0;
	}
	printf("[membership] updated from server: ");
	for (i = 0; i < RPCCORE_COUNT; i++) printf("core%d=%d ", i, rpc_is_core_present(i));
	printf("\n");
	rpc_on_membership_change();    /* redraw the menu (defined in main_file.c) */
}

/* Called from a receive thread when its link drops (recv returned <= 0). */
void rpc_handle_disconnect(enumFnCore eCore)
{
	if (get_this_core() == get_server_core())
	{
		/* A client dropped. Forget it and tell the remaining clients. */
		g_core_present[eCore]          = 0;
		sSocketInfo[eCore].Socket_connected = false;
		if (connect_count > 0) connect_count--;
		os_close_fd(sSocketInfo[eCore].data_fd);
		printf("[-] client core %d disconnected\n", eCore);
		rpc_broadcast_membership();
		rpc_on_membership_change();
	}
	else
	{
		/* The server (eCore) dropped - the whole network is gone for us. */
		int i;
		for (i = 0; i < RPCCORE_COUNT; i++)
		{
			if (i != get_this_core())
			{
				g_core_present[i]              = 0;
				sSocketInfo[i].Socket_connected = false;
			}
		}
		printf("[-] server core %d disconnected - all remote functions removed\n", eCore);
		rpc_on_membership_change();
	}
}

/* ------------------------------------------------------------------------
 * RPC topology settings. Config-file (rpc_config.cfg) support has been removed;
 * PORT and SERVER_CORE now come from their built-in defaults:
 *     PORT        = 8848    (the PORT macro; identical on every machine)
 *     SERVER_CORE = CORE2   (which core is the server/hub)
 * The server core is overridable via -S / setServerCore() / $RPC_SERVER_CORE, the
 * server IP via -s <ip> / $RPC_SERVER_IP (see main_file.c).
 * ---------------------------------------------------------------------- */
static int  g_config_port   = 0;
static int  g_config_loaded = 0;
static int  g_config_server_core = -1;   /* was SERVER_CORE from cfg; now always unset */

/* Server core chosen on the command line (-S <n>) or programmatically. Takes
 * precedence over $RPC_SERVER_CORE, so a core can be made the server with NO env
 * var. 0-based enum, -1 = unset. */
static int  g_server_core_override = -1;

/* core_num is the human core NUMBER (1..RPCCORE_COUNT). e.g. rpc_set_server_core(3)
 * makes CORE3 the server. */
void rpc_set_server_core(int core_num)
{
	if (core_num >= 1 && core_num <= RPCCORE_COUNT)
	{
		g_server_core_override = core_num - 1;
	}
}

/* Server IP supplied on the command line via -s (see main_file.c). This is now
 * the ONLY source of the server IP (falling back to $RPC_SERVER_IP, then
 * 127.0.0.1): a client is pointed at its server with  ./core1 -s 192.168.1.70  */
static char g_server_ip_override[64] = { 0 };

void rpc_set_server_ip(const char *ip)
{
	if (ip && ip[0])
	{
		strncpy(g_server_ip_override, ip, sizeof(g_server_ip_override) - 1);
		g_server_ip_override[sizeof(g_server_ip_override) - 1] = '\0';
	}
}

static char *rpc_trim(char *s)
{
	char *end;
	while (' ' == *s || '\t' == *s || '\r' == *s || '\n' == *s) s++;
	if ('\0' == *s) return s;
	end = s + strlen(s) - 1;
	while (end > s && (' ' == *end || '\t' == *end || '\r' == *end || '\n' == *end)) *end-- = '\0';
	return s;
}

/* rpc_config.cfg support has been REMOVED. PORT and SERVER_CORE now come from
 * their built-in defaults (the PORT macro / default CORE2), overridable at run
 * time by -S / setServerCore() / $RPC_SERVER_CORE. rpc_load_config() is kept as a
 * no-op so its callers (get_server_core / rpc_config_port) link unchanged; the
 * former file parser is preserved below under #if 0 for reference. */
static void rpc_load_config(void)
{
	g_config_loaded = 1;   /* nothing to load; keeps the flag referenced */
	return;
#if 0   /* ==== former rpc_config.cfg loader (disabled) ==== */
	/* Search order: $RPC_CONFIG, ./rpc_config.cfg, /data/local/tmp/rpc_config.cfg */
	const char *paths[3];
	const char *env_path = getenv("RPC_CONFIG");
	FILE       *fp       = NULL;
	int         i;
	char        line[128];

	if (g_config_loaded) return;
	g_config_loaded = 1;

	paths[0] = (env_path && env_path[0]) ? env_path : NULL;
	paths[1] = "rpc_config.cfg";
	paths[2] = "/data/local/tmp/rpc_config.cfg";

	for (i = 0; i < 3; i++)
	{
		if (NULL == paths[i]) continue;
		fp = fopen(paths[i], "r");
		if (fp != NULL)
		{
			printf("rpc_load_config(): using config file %s\n", paths[i]);
			break;
		}
	}
	if (NULL == fp)
	{
		printf("rpc_load_config(): no config file found, using PORT=%d default and SERVER_CORE default\n", PORT);
		return;
	}

	while (fgets(line, sizeof(line), fp) != NULL)
	{
		char *hash = strchr(line, '#');
		char *eq;
		char *key;
		char *val;
		if (hash) *hash = '\0';
		eq = strchr(line, '=');
		if (NULL == eq) continue;
		*eq = '\0';
		key = rpc_trim(line);
		val = rpc_trim(eq + 1);
		if ('\0' == val[0]) continue;

		/* COREx_IP is intentionally ignored/removed - the server IP now comes
		 * from -s on the command line, not from the config file. */
		if      (0 == strcmp(key, "PORT"))     g_config_port = atoi(val);
		else if (0 == strcmp(key, "SERVER_CORE"))
		{
			/* Config uses the human core NUMBER (1,2,3); map to the 0-based enum. */
			int n = atoi(val);
			if (n >= 1 && n <= RPCCORE_COUNT) g_config_server_core = n - 1;
		}
	}
	fclose(fp);
#endif  /* ==== former rpc_config.cfg loader (disabled) ==== */
}

/* Which core acts as the server/hub. Precedence: -S / setServerCore() >
 * $RPC_SERVER_CORE (core number 1..3) > default CORE2. Every core in the
 * network MUST agree on this value. */
enumFnCore get_server_core(void)
{
	const char *env = getenv("RPC_SERVER_CORE");
	rpc_load_config();
	/* Command-line / programmatic override wins over the env var. */
	if (g_server_core_override >= 0) return (enumFnCore)g_server_core_override;
	if (env && env[0])
	{
		int n = atoi(env);
		if (n >= 1 && n <= RPCCORE_COUNT) return (enumFnCore)(n - 1);
	}
	if (g_config_server_core >= 0) return (enumFnCore)g_config_server_core;
	return RPCCORE_CORE2;   /* backward-compatible default */
}

/* Set when this core is pointed at a server with -s <ip> (or, on Android, is a
 * client by topology). A core given -s is a CLIENT even if it happens to be the
 * default server core (CORE2) - so clients only need -s <ip>, never -S <n>. */
static int g_am_client = 0;

void rpc_set_client_mode(void)
{
	g_am_client = 1;
}

/* Single source of truth for the client/server role decision, used by both
 * open_comm() and rpc_init(). Precedence:
 *   1. explicit  -S <self> / -server  (override names THIS core)  -> SERVER
 *   2. -s <ip> was given (client marker)                          -> CLIENT
 *   3. otherwise this core == get_server_core() (env/default) -> SERVER
 * This is why core2/core1 need only -s <ip>: -s forces client, overriding the
 * CORE2 default, while the real server is named explicitly with -S/-server. */
int rpc_this_core_is_server(void)
{
	enumFnCore me = get_this_core();
	if (g_server_core_override == (int)me) return 1;   /* explicit -S self / -server */
	if (g_am_client)                       return 0;   /* -s => client */
	return (me == get_server_core());
}

/* IP to reach the server. Precedence: command-line -s (rpc_set_server_ip) >
 * $RPC_SERVER_IP > 127.0.0.1. IPs are never read from a config file. eCore is
 * unused now (a client only ever connects to the one server), kept for API
 * compatibility with the callers. */
const char *rpc_config_core_ip(enumFnCore eCore)
{
	const char *env_ip = getenv("RPC_SERVER_IP");
	(void)eCore;
	if (g_server_ip_override[0])   return g_server_ip_override;
	if (env_ip && env_ip[0])       return env_ip;
	return "127.0.0.1";
}

/* Base port: the PORT macro default (config-file support removed). */
int rpc_config_port(void)
{
	rpc_load_config();
	return (g_config_port > 0) ? g_config_port : PORT;
}

void os_memclr(void* ptr, int iSize)
{
	memset ( ptr, 0 , iSize ) ;
}



void os_memcpy( void* dest, void* src , int iLen)
{
	memcpy ( dest , src , iLen ) ;
}



void* os_alloc_rpc_buffer(enumFnCore eCore, int iSize)
{
	void* ptr = NULL;
	switch (eCore)
	{
	case RPCCORE_CORE1:
		ptr = (void*)malloc(iSize);
		break;

	case RPCCORE_CORE2:
		ptr = (void*)malloc(iSize);
		break;

	case RPCCORE_CORE3:
		ptr = (void*)malloc(iSize);
		break;

	default:
		break;
	}
	
	return ptr;
	
}

	
	
void* os_create_semaphore(void)
{
#if defined(_WIN32)
	/* Counting semaphore, initial count 0, max count = LONG_MAX */
	HANDLE h_sem = CreateSemaphore(NULL, SEMAPHORE_INIT_VALUE, LONG_MAX, NULL);
	if (NULL == h_sem)
	{
		printf("Error in Semaphore Initialization\n");
	}
	else
	{
		printf("Semaphore created successfully\n");
	}
	return (void*)h_sem;
#else
	sem_t *ps_semaphore = malloc(sizeof(sem_t));
	/*sem_init would return 0 on success*/
	if (!(sem_init(ps_semaphore, 1, SEMAPHORE_INIT_VALUE)==0) )
	{
		printf("Error in Semaphore Initialization\n");
	}
	else
	{
		printf("Semaphore created successfully\n");
	}
	return ps_semaphore;
#endif
}



void os_sem_post(void* p_sem)
{
#if defined(_WIN32)
	if (!ReleaseSemaphore((HANDLE)p_sem, 1, NULL))
	{
		printf("Error in Semaphore post\n");
	}
#else
	if (!(sem_post(p_sem)==0) )
	{
		printf("Error in Semaphore post\n");
	}
#endif
}



int os_sem_pend(void* p_sem, int wait_time)
{
	(void)wait_time;   /* timeout not honoured yet (see #if 0 block below) */
#if defined(_WIN32)
	DWORD dw = WaitForSingleObject((HANDLE)p_sem, INFINITE);
	return (WAIT_OBJECT_0 == dw) ? 0 : -1;
#else
	int s = sem_wait(p_sem);
	return s;
#endif
#if 0
	struct timespec ts;
	int s;
	int errno;

	if (clock_gettime(CLOCK_REALTIME, &ts) == -1)
	{
	    printf("Not able to get epoch time\n");
	    return -1;
	}
	printf("time before adding WAIT = %d\n",ts.tv_sec);
	ts.tv_sec += wait_time/1000;//wait time is passed as an agrument in milli seconds
	printf("time after adding WAIT = %d\n",ts.tv_sec);
	while ((s = sem_timedwait((void*)p_sem, &ts)) == -1 && errno == EINTR)
	               continue;       /* Restart if interrupted by handler */
	/* Check what happened */
	if (s == -1)
	{
	    if (errno == ETIMEDOUT)
	        printf("sem_timedwait() timed out\n");
	    else
	        perror("sem_timedwait");
	} else
	        printf("sem_timedwait() succeeded\n");


	return s;
#endif
}



void* os_create_mutex(void)
{
#if defined(_WIN32)
	CRITICAL_SECTION *p_cs = malloc ( sizeof(CRITICAL_SECTION) );
	if (NULL != p_cs)
	{
		InitializeCriticalSection(p_cs);
		printf("Mutex creation successful\n");
	}
	return (void*)p_cs;
#else
	pthread_mutex_t *p_mutex = malloc ( sizeof(pthread_mutex_t)) ;
	if (pthread_mutex_init(p_mutex, NULL) == 0)
	{
		printf("Mutex creation successful\n");
	}
	return (p_mutex);
#endif
}



void os_lock(void* mtx)
{
#if defined(_WIN32)
	EnterCriticalSection((CRITICAL_SECTION*)mtx);
#else
	pthread_mutex_lock(mtx);
#endif
}



void os_unlock(void* mtx)
{
#if defined(_WIN32)
	LeaveCriticalSection((CRITICAL_SECTION*)mtx);
#else
	pthread_mutex_unlock(mtx);
#endif
}



void os_create_thread(void* p_fn, enumFnCore eCore)
{
	if (eCore >= RPCCORE_COUNT)
	{
		return;
	}
#if defined(_WIN32)
	HANDLE h_thread = CreateThread(NULL, 0,
	                               (LPTHREAD_START_ROUTINE)p_fn,
	                               (LPVOID)(intptr_t)eCore, 0, NULL);
	if (NULL != h_thread)
	{
		CloseHandle(h_thread);   /* detach: the thread keeps running */
	}
#else
	pthread_t P_Thread;
	pthread_create(&P_Thread, NULL, (void *(*)(void *))p_fn, (void*)eCore);
#endif
}



void os_free_rpc_buffer (enumFnCore eCore, void* ptr, int iSize)
{
	switch (eCore) 
	{
	case RPCCORE_CORE1:
		free(ptr);

		break;

	case RPCCORE_CORE2:
		free(ptr);

		break;

	case RPCCORE_CORE3:
		free(ptr);

		break;

	default:
		break;
	}
}

/* SERVER: bind + listen the ONE shared port, once, before any client is
 * accepted. Called from rpc_init() (single-threaded) so the per-client accept
 * threads can then share g_listen_fd. Returns 0 on success, -1 on failure. */
int os_server_listen_init(void)
{
	struct sockaddr_in addr;
	int                reuse = 1;

	if (g_listen_fd != OS_INVALID_SOCKET) return 0;   /* already listening */

	g_listen_fd = socket(AF_INET, SOCK_STREAM, 0);
	if (g_listen_fd == OS_INVALID_SOCKET)
	{
		printf("os_server_listen_init() : socket() failed\n");
		return -1;
	}

	/* Allow immediate re-bind after a restart instead of waiting out TIME_WAIT. */
	setsockopt(g_listen_fd, SOL_SOCKET, SO_REUSEADDR, (const char *)&reuse, sizeof(reuse));

	memset(&addr, 0, sizeof(addr));
	addr.sin_family      = AF_INET;
	addr.sin_addr.s_addr = htonl(INADDR_ANY);
	addr.sin_port        = htons(rpc_config_port());   /* SINGLE shared port for all clients */

	if (bind(g_listen_fd, (struct sockaddr *)&addr, sizeof(addr)) != 0)
	{
		printf("os_server_listen_init() : bind failed on port %d - returning error (no exit)\n",
		       rpc_config_port());
		os_close_fd(g_listen_fd);
		g_listen_fd = OS_INVALID_SOCKET;
		return -1;
	}

	if (listen(g_listen_fd, RPCCORE_COUNT + 2) != 0)
	{
		printf("os_server_listen_init() : listen failed - returning error (no exit)\n");
		os_close_fd(g_listen_fd);
		g_listen_fd = OS_INVALID_SOCKET;
		return -1;
	}

	printf("os_server_listen_init() : SERVER core %d listening on shared port %d for all clients\n",
	       get_this_core(), rpc_config_port());
	return 0;
}

/* SERVER: accept ONE client on the shared listen socket and identify it via the
 * handshake. On success *peAcceptedCore holds the connecting client's core id
 * and its sSocketInfo[] slot is populated. Returns 0 on success, -1 on failure.
 * Multiple accept threads may call this concurrently on g_listen_fd; whichever
 * thread wins a given connection services that core. */
int socket_server_init(enumFnCore *peAcceptedCore)
{
	struct sockaddr_in cliaddr;
	socklen_t          len = sizeof(cliaddr);
	os_socket_t        data_fd;
	enumFnCore         eClientCore;

	if (g_listen_fd == OS_INVALID_SOCKET)
	{
		printf("socket_server_init() : no shared listen socket (os_server_listen_init failed?)\n");
		return -1;
	}

	printf("socket_server_init() : core %d (SERVER) awaiting a client on shared port %d ...\n",
	       get_this_core(), rpc_config_port());

	data_fd = accept(g_listen_fd, (struct sockaddr *)&cliaddr, &len);
	if (data_fd == OS_INVALID_SOCKET)
	{
		printf("socket_server_init() : accept failed - returning error (no exit)\n");
		return -1;
	}

	/* Read the client's core id BEFORE we know which slot this connection is. */
	if (0 != io_handshake_recv(data_fd, &eClientCore))
	{
		printf("socket_server_init() : handshake read failed - dropping connection\n");
		os_close_fd(data_fd);
		return -1;
	}

	/* Now we know the core: clear and populate its slot. */
	memset(&sSocketInfo[eClientCore], 0, sizeof(StructSocketInfo));
	sSocketInfo[eClientCore].connect_fd      = g_listen_fd;   /* shared; closed once in os_close_socket */
	sSocketInfo[eClientCore].data_fd         = data_fd;
	sSocketInfo[eClientCore].Socket_connected = true;
	g_core_present[eClientCore] = 1;         /* this client is now in the network */
	connect_count++;
	printf("socket_server_init() : ACCEPT ok for client core %d (DATA_FD=%d CONNECT_COUNT=%d)\n",
	       eClientCore, (int)data_fd, connect_count);

	sSocketInfo[eClientCore].p_send_mutex = os_create_mutex();

	*peAcceptedCore = eClientCore;

	/* New client fully connected: tell every client the new membership and
	 * redraw our own menu (after the mutex exists, since broadcast sends). */
	rpc_broadcast_membership();
	rpc_on_membership_change();
	return 0;
}


int socket_client_init(enumFnCore eRemoteCore)
{
	int         connect_result = -1;
	int         attempt;
	const int   max_attempts = 10;   /* ~10s of retries; tolerates server-not-up-yet */
	int         i = 0;
	enumFnCore  eThisCore  = get_this_core();
	/* Server address comes only from -s (rpc_set_server_ip) / $RPC_SERVER_IP /
	 * 127.0.0.1 - never the config file. */
	const char *server_ip   = rpc_config_core_ip(eRemoteCore);
	int         server_port = rpc_config_port();   /* SINGLE shared port on the hub (same for every client) */

	for (attempt = 1; attempt <= max_attempts; attempt++)
	{
		sSocketInfo[eRemoteCore].connect_fd = socket ( AF_INET , SOCK_STREAM , 0 );
		if (sSocketInfo[eRemoteCore].connect_fd == OS_INVALID_SOCKET)
		{
			printf("socket_client_init(): socket() failed (attempt %d)\n", attempt);
			os_sleep_sec(1);
			continue;
		}
		sSocketInfo[eRemoteCore].data_fd = sSocketInfo[eRemoteCore].connect_fd;

		sSocketInfo[eRemoteCore].addr.sin_family      = AF_INET;
		sSocketInfo[eRemoteCore].addr.sin_addr.s_addr = inet_addr(server_ip);
		sSocketInfo[eRemoteCore].addr.sin_port        = htons(server_port);

		printf("socket_client_init(): attempt %d/%d -> server %s:%d\n",
		       attempt, max_attempts, server_ip, server_port);
		connect_result = connect ( sSocketInfo[eRemoteCore].connect_fd,
					   (struct sockaddr*)&sSocketInfo[eRemoteCore].addr,
					   sizeof(struct sockaddr) );
		if (connect_result == 0) break;

		printf("socket_client_init(): connect failed (attempt %d) - retry in 1s\n", attempt);
		os_close_fd(sSocketInfo[eRemoteCore].connect_fd);
		sSocketInfo[eRemoteCore].connect_fd = OS_INVALID_SOCKET;
		os_sleep_sec(1);
	}

	if (connect_result != 0)
	{
		/* Give up WITHOUT exiting - on Android this must not kill the app. The
		 * caller (rpc_recv) sets g_rpc_link_failed so main()/JNI can report it. */
		printf("socket_client_init(): could NOT connect to server core %d after %d attempts (no exit)\n",
		       eRemoteCore, max_attempts);
		return -1;
	}

	/* Announce our core id so the single-port hub knows who just connected.
	 * Must happen before we mark the link up / start relaying. */
	if (0 != io_handshake_send(sSocketInfo[eRemoteCore].data_fd, eThisCore))
	{
		printf("socket_client_init(): handshake send failed to server core %d (no exit)\n", eRemoteCore);
		os_close_fd(sSocketInfo[eRemoteCore].connect_fd);
		sSocketInfo[eRemoteCore].connect_fd = OS_INVALID_SOCKET;
		return -1;
	}

	sSocketInfo[eRemoteCore].Socket_connected = true;
	g_core_present[eRemoteCore] = 1;    /* the server is present */
	connect_count++;                    /* we hold ONE real link (to the server) */
	printf("socket_client_init(): connected to server core %d (announced self as core %d)\n",
	       eRemoteCore, eThisCore);
	sSocketInfo[eRemoteCore].p_send_mutex = os_create_mutex ();

	for ( i = 0 ; i < RPCCORE_COUNT ; i++ )
	{
		if ( (i != eThisCore ) && ( i != eRemoteCore ))
		{
			sSocketInfo[i] = sSocketInfo[eRemoteCore];   /* peer slots relay via server */
		}
	}
	/* Show the server's functions now; peers arrive via the server's broadcast. */
	rpc_on_membership_change();
	return 0;
}
/* Socket init called to receive data from eCore. Returns 0 on success, -1 on
 * failure (caller decides what to do - no exit() here). */
int open_comm ( enumFnCore *peCore )
{
	printf("open_comm(): this=%d remote(hint)=%d server=%d\n",
	       get_this_core(), *peCore, get_server_core());

	/* Role is decided by rpc_this_core_is_server() (see osal.c): explicit
	 * -S/-server wins, then -s forces client, then env/default. The server
	 * accepts clients on ONE shared port and identifies each via the handshake
	 * (so *peCore is an OUTPUT here - the core that actually connected); every
	 * other core connects to the server on that same shared port. */
	if (rpc_this_core_is_server())
	{
		return socket_server_init(peCore);        /* accept a client; *peCore := its core */
	}
	return socket_client_init(*peCore);           /* connect to the server (*peCore) */
}

int os_recv_rpc_buffer (enumFnCore eRemoteCore, void** ptr)
{
	int read_size = 0;
	void* p_rpcbuff;
	p_rpcbuff = malloc(SOCKET_READSIZE);
	*ptr= p_rpcbuff;

	if  ( ( NULL != p_rpcbuff ) && (sSocketInfo[eRemoteCore].Socket_connected) )
	{
		read_size = os_read(sSocketInfo[eRemoteCore].data_fd, p_rpcbuff , SOCKET_READSIZE);
	}
	else
	{
		//printf("Socket connection failure in recv\n");
	}

	return read_size;
}
//send data in *ptr to eCore
enumRpcErr os_send_rpc_buffer (enumFnCore eRemoteCore, int iSize , void* ptr)
{
	int sent_size = 0;

	enumRpcErr eError = RPCERR_SUCCESS ;

	if (sSocketInfo[eRemoteCore].Socket_connected)/* send data only if the sockets are connected */
	{
		if (sSocketInfo[eRemoteCore].p_send_mutex)/*check if mutex is created */
		{
			os_lock(sSocketInfo[eRemoteCore].p_send_mutex);
			//printf("^^^^^^^^^^^^^^^^  MUTEX LOCKED in %d ^^^^^^^^^^^^^^^^^^^^^^^^^^\n",eRemoteCore);
			sent_size = os_write(sSocketInfo[eRemoteCore].data_fd, (void*)ptr, iSize);//write returns the size of data written to client
			printf("Sent %d bytes to Core : %d\n",sent_size, eRemoteCore);

			if ( sent_size == 0 )
			{
				eError = RPCERR_SEND_FAILED;
			}
			os_unlock(sSocketInfo[eRemoteCore].p_send_mutex);
			//printf("^^^^^^^^^^^^^^^^ MUTEX UNLOCKED in %d ^^^^^^^^^^^^^^^^^^^^^^^^\n",eRemoteCore);
		}
		else
		{
			printf("######### ERROR : MUTEX not created ############\n");
		}
	}

	else
	{
		eError = RPCERR_SEND_FAILED;
		printf("%d Corec Socket not connected to ore  %d\n",get_this_core(),eRemoteCore);
	}

	return eError;
}
	
void os_close_socket(void)
{
	int iLoop = 0;

	for ( iLoop = 0 ; iLoop < RPCCORE_COUNT ; iLoop++ )
	{
		os_close_fd(sSocketInfo[iLoop].data_fd);
	}

	/* Close the single shared listen socket (server only; invalid elsewhere). */
	if (g_listen_fd != OS_INVALID_SOCKET)
	{
		os_close_fd(g_listen_fd);
		g_listen_fd = OS_INVALID_SOCKET;
	}
}

/* ****************************************************************************
 * Network stack init / teardown.
 * On Windows, Winsock must be initialised with WSAStartup() before any socket
 * call and cleaned up with WSACleanup(). On POSIX these are no-ops.
 * ****************************************************************************/
void os_net_init(void)
{
#if defined(_WIN32)
	WSADATA wsa_data;
	int i_res = WSAStartup(MAKEWORD(2, 2), &wsa_data);
	if (0 != i_res)
	{
		printf("os_net_init(): WSAStartup() failed, error = %d\n", i_res);
	}
#endif
}

void os_net_cleanup(void)
{
#if defined(_WIN32)
	WSACleanup();
#endif
}
