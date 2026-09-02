/*
 * Copyright (c) 2026 Prajnaana Technologies
 * SPDX-License-Identifier: MIT
 *
 * Part of the Multi-Core RPC Framework.
 * See the LICENSE file in the project root for the full license text.
 *
 * Original Author: Mamatha BV
 */

#include "os_platform.h"       /* OS-selected system headers + primitives */
#include "rpc_marshall.h"

#define SEMAPHORE_INIT_VALUE 	0
#define PORT					8848
#define SOCKET_READSIZE 		1024
typedef struct tagSocketInfo
{
	os_socket_t connect_fd;
	struct sockaddr_in addr;
	os_socket_t data_fd;
	bool Socket_connected;
	void *p_send_mutex;
}StructSocketInfo;

void os_memclr(void* ptr, int isize);
void os_memcpy(void* dest, void* src, int iLen);
void* os_alloc_rpc_buffer(enumFnCore eCore, int iSize);
void os_lock(void* mtx);
void os_unlock(void* mtx);
void* os_create_semaphore(void);
void* os_create_mutex(void); 
void os_sem_post(void*);
int os_sem_pend(void* p_sem,int wait_time);
void os_create_thread(void * p_fn , enumFnCore eCore);
void os_free_rpc_buffer (enumFnCore eCore, void* ptr, int iSize);
int os_recv_rpc_buffer (enumFnCore eCore, void** ptr);
enumRpcErr os_send_rpc_buffer (enumFnCore eCore, int iSize , void* ptr);
int  open_comm(enumFnCore *peCore);        /* 0 = link established, -1 = failed (no exit); server: *peCore is OUT (accepted core) */
int  os_server_listen_init(void);          /* SERVER: bind+listen the single shared port once (0=ok, -1=fail) */
int  socket_server_init(enumFnCore *peAcceptedCore); /* accept one client on the shared port; *peAcceptedCore := its core */
int  socket_client_init(enumFnCore eCore); /* 0 = connected, -1 = gave up after retries */
extern volatile int g_rpc_link_failed;     /* set when a CLIENT cannot reach its server */
void rpc_set_server_ip(const char *ip);            /* set server IP from command line (-s) */
const char *rpc_config_core_ip(enumFnCore eCore);  /* server IP: -s > $RPC_SERVER_IP > 127.0.0.1 (no longer from config) */
int rpc_config_port(void);                         /* base port (the PORT macro default) */
enumFnCore get_server_core(void);                  /* which core is the server/hub (-S / RPC_SERVER_CORE env, default CORE2) */
void rpc_set_server_core(int core_num);            /* pick server core (1..3) via -S flag; wins over env */
void rpc_set_client_mode(void);                    /* -s <ip> => this core is a client (needs no -S) */
int  rpc_this_core_is_server(void);                /* role decision used by open_comm()/rpc_init() */

/* ---- Dynamic membership (which cores are in the network) ---------------- */
extern int g_core_present[RPCCORE_COUNT];
int  rpc_is_core_present(enumFnCore c);             /* 1 if core c's functions are usable (self or present) */
void rpc_apply_membership(const unsigned char *flags); /* CLIENT: adopt server's bitmap */
void rpc_handle_disconnect(enumFnCore eCore);       /* a link dropped */
void rpc_broadcast_membership(void);                /* SERVER: push bitmap to all clients (in rpc_marshall.c) */
void rpc_on_membership_change(void);                /* redraw the menu (in main_file.c) */
void os_close_socket(void);
void os_net_init(void);      /* WSAStartup on Windows, no-op on POSIX  */
void os_net_cleanup(void);   /* WSACleanup on Windows, no-op on POSIX  */
