/*
 * Copyright (c) 2026 Prajnaana Technologies
 * SPDX-License-Identifier: MIT
 *
 * Part of the Multi-Core RPC Framework.
 * See the LICENSE file in the project root for the full license text.
 *
 * Original Author: Mamatha BV
 */

/*
 * main_file.c
 *
 *  Created on: May 8, 2023
 *      Author: mc
 */
#define RPCHDR_START_SIGN           (0x50A0050Au)
#define RPCHDR_END_SIGN             (0xAF5FFAF5u)

#include <stdio.h>

#include "rpc_fn.h"

#include "rpc_fncode.h"
#include "osal.h"
extern void rpc_init(void);
extern StructSocketInfo sSocketInfo[RPCCORE_COUNT];
extern int connect_count;

/* ===========================================================================
 * "rpc>" command prompt for the rpc_fn.h function set.
 * The menu is DYNAMIC: each command is tagged with the core that owns it, and a
 * command is shown as available only when that core is present in the network
 * (self is always present). As cores connect/disconnect, the server broadcasts
 * membership and rpc_on_membership_change() redraws this list on every core.
 * =========================================================================== */
typedef struct
{
	const char *key;     /* what the user types            */
	const char *usage;   /* how to type it                 */
	const char *desc;    /* what it does                   */
	enumFnCore  core;    /* which core owns the function    */
} MenuItem;

static const MenuItem g_menu[] =
{
	{ "mod",       "mod <x> <y>",   "fn_compute_mod        -> x % y",        RPCCORE_CORE1 },
	{ "point",     "point <x> <y>", "fn_print_point_core1  -> (x+1, y+1)",   RPCCORE_CORE1 },
	{ "ver_core1", "ver_core1",     "fn_get_rpc_version_core1",              RPCCORE_CORE1 },
	{ "sqr",       "sqr <x>",       "fn_compute_sqr        -> x*x",          RPCCORE_CORE2 },
	{ "ver_core2", "ver_core2",     "fn_get_rpc_version_core2",              RPCCORE_CORE2 },
	{ "hello",     "hello <text>",  "fn_print_hello",                       RPCCORE_CORE3 },
	{ "array",     "array <n...>",  "fn_array_core3 -> prints/echoes your ints", RPCCORE_CORE3 },
	{ "ver_core3", "ver_core3",     "fn_get_rpc_version_core3",              RPCCORE_CORE3 },
};
#define MENU_COUNT ((int)(sizeof(g_menu) / sizeof(g_menu[0])))

static const MenuItem *find_menu(const char *cmd)
{
	int i;
	for (i = 0; i < MENU_COUNT; i++)
	{
		if (0 == strcmp(cmd, g_menu[i].key)) return &g_menu[i];
	}
	return NULL;
}

static void print_help(void)
{
	int        i;
	enumFnCore me = get_this_core();

	/* Core numbers are shown 1-based (CORE1/CORE2/CORE3), matching the -S <n> /
	 * SERVER_CORE <n> convention. Every function is prefixed with the core that
	 * OWNS (implements) it, followed by an availability tag. */
	printf("\n===== CORE%d : function menu  ([COREn] = core that implements the function) =====\n",
	       me + 1);
	for (i = 0; i < MENU_COUNT; i++)
	{
		enumFnCore  c      = g_menu[i].core;
		const char *status = (c == me)               ? "self"
		                   : rpc_is_core_present(c)   ? "connected"
		                                              : "NOT connected";
		printf("  [CORE%d] %-14s %-38s (%s)\n",
		       c + 1, g_menu[i].usage, g_menu[i].desc, status);
	}
	printf("  help | quit\n");
}

/* Called (from a receive thread) whenever membership changes, so the live menu
 * reflects a core joining or leaving without the user having to type 'help'. */
void rpc_on_membership_change(void)
{
	printf("\n----- function menu updated -----\n");
	print_help();
	printf("\nrpc> ");
	fflush(stdout);
}

static void rpc_prompt(void)
{
	char   line[128];
	char   cmd[32];
	char   text[128];   /* room for the "Hello " prefix CORE3 adds (INOUT echo) */
	int    x, y, r, i;
	sPoint pin, pout;
	const MenuItem *mi_tmp;

	printf("\n===== core %d : RPC command prompt (TEST set) =====\n", get_this_core());
	print_help();

	for (;;)
	{
		printf("\nrpc> ");
		if (NULL == fgets(line, sizeof(line), stdin))  break;
		if (1 != sscanf(line, "%31s", cmd))            continue;

		if (0 == strcmp(cmd, "quit") || 0 == strcmp(cmd, "exit"))
		{
			break;
		}
		else if (0 == strcmp(cmd, "help"))
		{
			print_help();
		}
		else if ((mi_tmp = find_menu(cmd)) != NULL && !rpc_is_core_present(mi_tmp->core))
		{
			printf("  '%s' is on CORE%d, which is NOT connected yet.\n", cmd, mi_tmp->core + 1);
		}
		else if (0 == strcmp(cmd, "mod"))
		{
			if (2 == sscanf(line, "%*s %d %d", &x, &y))
			{
				r = 0;
				fn_compute_mod(x, y, &r);
				printf("  fn_compute_mod(%d, %d) = %d\n", x, y, r);
			}
			else printf("  usage: mod <x> <y>\n");
		}
		else if (0 == strcmp(cmd, "sqr"))
		{
			if (1 == sscanf(line, "%*s %d", &x))
			{
				r = 0;
				fn_compute_sqr(x, &r);
				printf("  fn_compute_sqr(%d) = %d\n", x, r);
			}
			else printf("  usage: sqr <x>\n");
		}
		else if (0 == strcmp(cmd, "point"))
		{
			if (2 == sscanf(line, "%*s %d %d", &x, &y))
			{
				pin.x = x; pin.y = y; pout.x = 0; pout.y = 0;
				fn_print_point_core1(pin, &pout);
				printf("  fn_print_point_core1 -> (%d, %d)\n", pout.x, pout.y);
			}
			else printf("  usage: point <x> <y>\n");
		}
		else if (0 == strcmp(cmd, "ver_core1")) { r = 0; fn_get_rpc_version_core1(&r); printf("  core1 version = %d\n", r); }
		else if (0 == strcmp(cmd, "ver_core2")) { r = 0; fn_get_rpc_version_core2(&r); printf("  core2 version = %d\n", r); }
		else if (0 == strcmp(cmd, "ver_core3")) { r = 0; fn_get_rpc_version_core3(&r); printf("  core3 version = %d\n", r); }
		else if (0 == strcmp(cmd, "hello"))
		{
			text[0] = '\0';
			sscanf(line, "%*s %99[^\n]", text);
			if ('\0' == text[0])  { strcpy(text, "world"); }
			/* iSize sized for "Hello " (6) + text + '\0'. CORE3 prefixes and echoes
			 * the result back into text (INOUT). */
			fn_print_hello((int)strlen(text) + 7, text);
			printf("  CORE3 echoed: \"%s\"\n", text);
		}
		else if (0 == strcmp(cmd, "array"))
		{
			/* Parse the space-separated ints the user typed after "array".
			 * e.g.  array 5 10 15 20      (up to 16). Defaults to 1 2 3 4 if none. */
			int   arr[16];
			int   n = 0;
			char *p = line;
			/* skip the command word "array" */
			while (*p && *p != ' ' && *p != '\t') p++;
			while (*p && n < 16)
			{
				int consumed = 0;
				if (1 == sscanf(p, "%d%n", &arr[n], &consumed) && consumed > 0)
				{
					n++;
					p += consumed;
				}
				else
				{
					p++;   /* skip separators / non-numeric chars */
				}
			}
			if (0 == n) { arr[0]=1; arr[1]=2; arr[2]=3; arr[3]=4; n = 4; }
			fn_array_core3(n, arr);
			printf("  sent array of %d, CORE3 echoed: [", n);
			for (i = 0; i < n; i++) printf("%d%s", arr[i], (i < n-1) ? " " : "");
			printf("]\n");
		}
		else
		{
			printf("  unknown command '%s' (type 'help')\n", cmd);
		}
	}
}

int main(int argc, char *argv[])
{

	enumRpcErr eRR = 2;
	setvbuf(stdout, NULL, _IONBF, 0);  /* unbuffered logs (visible when piped/redirected) */

	/* Server (CORE2) IP address. A client is pointed at its server with the
	 * -s option:
	 *     ./rpc_core1 -s 192.168.1.70
	 * When omitted, we fall back to $RPC_SERVER_IP, then 127.0.0.1. The server
	 * (CORE2) ignores this - it binds all interfaces. */
	{
		const char *server_ip = NULL;
		int         i;
		for (i = 1; i < argc; i++)
		{
			if (0 == strcmp(argv[i], "-s"))
			{
				if (i + 1 < argc)
				{
					server_ip = argv[++i];
				}
				else
				{
					printf("main(): -s given without an IP address\n");
				}
			}
			/* -S declares the SERVER. Two forms:
			 *   -S          -> THIS core is the server   (e.g. ./core3 -S)
			 *   -S <n>      -> core <n> (1..3) is the server (rarely needed; clients
			 *                  only need -s <ip> and are auto-detected as clients).
			 * No config-file edit or env var required. -server/--server are aliases. */
			else if (0 == strcmp(argv[i], "-S")       || 0 == strcmp(argv[i], "--server-core") ||
			         0 == strcmp(argv[i], "-server")  || 0 == strcmp(argv[i], "--server"))
			{
				/* Optional numeric core after -S (a bare "1".."3"). Anything else
				 * (missing, or an IP/flag) means "this core is the server". */
				if ((i + 1 < argc) &&
				    (argv[i + 1][0] >= '1') && (argv[i + 1][0] <= '3') && (argv[i + 1][1] == '\0'))
				{
					rpc_set_server_core(atoi(argv[++i]));
				}
				else
				{
					rpc_set_server_core((int)get_this_core() + 1);   /* bare -S => self */
				}
				printf("main(): SERVER core declared: %d (this core = %d)\n",
				       get_server_core() + 1, get_this_core() + 1);
			}
		}
		if (server_ip)
		{
			rpc_set_server_ip(server_ip);
			rpc_set_client_mode();   /* -s <ip> => this core is a client (no -S needed) */
			printf("main(): server IP set from command line: %s (this core is a CLIENT)\n", server_ip);
		}
		else
		{
			printf("main(): no server IP given (usage: %s -s <server_ip>);"
			       " using $RPC_SERVER_IP / 127.0.0.1\n", argv[0]);
		}
	}

	os_net_init();      /* initialise Winsock on Windows (no-op on POSIX) */
	rpc_init();

	/* No fixed "wait for 2 connections" anymore. A CLIENT waits only until it is
	 * connected to its server (so it can issue calls); the menu then fills in as
	 * the server broadcasts who else is present. The SERVER enters the prompt
	 * right away showing just its own functions, and each client that connects
	 * is added to the menu on every core. */
	/* Role comes from rpc_this_core_is_server() so a client given only -s <ip>
	 * (no -S) is handled correctly even when it is the CORE2 default. A CLIENT
	 * waits until it holds a link (connect_count>=1) rather than for a specific
	 * server-core index, since -s clients don't need to name the server core. */
	if (!rpc_this_core_is_server())
	{
		printf("main(): CLIENT - waiting to connect to the server ...\n");
		while ((connect_count < 1) && !g_rpc_link_failed)
		{
			os_sleep_sec(1);
		}
		if (g_rpc_link_failed)
		{
			/* Could not reach the server. Exit cleanly with a message instead of
			 * hanging or crashing (a JNI wrapper would return this as an error). */
			printf("main(): could not connect to the server - exiting.\n");
			os_close_socket();
			os_net_cleanup();
			return 1;
		}
		printf("main(): connected to the server\n");
	}
	else
	{
		printf("main(): SERVER core %d ready - clients will be added as they connect\n",
		       get_this_core());
	}
#if 0
	int    Result = 0;
	sPoint sp, sP_out;
	int    arr[4] = {1,2,3,4};
	sp.x = 3;
	sp.y = 6;
	eRR = RPCERR_SEND_FAILED;
	while ( (eRR != RPCERR_SUCCESS) && ( eRR == RPCERR_SEND_FAILED))
	{
		eRR = fn_get_rpc_version_core2(&Result);
		if (eRR != RPCERR_SUCCESS )
		{
			printf("\t\t!!!!!!!!!!ERROR CORE2 Result = %d, retrying SEND....\n",eRR);
			sleep(4);

		}
		else
		{
			printf(" \t\t **** main(): CORE2 RESULT = %d\n",Result);
		}

	}



	Result = 0;
	eRR = RPCERR_SEND_FAILED;
	while ( (eRR != RPCERR_SUCCESS) && ( eRR == RPCERR_SEND_FAILED))
	{
		eRR = fn_get_rpc_version_core1(&Result);
		if (eRR != RPCERR_SUCCESS )
		{
			printf("\t\t!!!!!!!!!! ERROR CORE1 Result = %d, retrying SEND ...\n",eRR);
			sleep(4);
		}
		else
		{
			printf("\t\t **** main(): CORE1 RESULT = %d\n",Result);
		}
	}
	Result = 0;
	eRR = RPCERR_SEND_FAILED;
	while ( (eRR != RPCERR_SUCCESS) && ( eRR == RPCERR_SEND_FAILED))
	{
		eRR = fn_print_point_core1 (sp, &sP_out);
		if (eRR != RPCERR_SUCCESS )
		{
			printf("ERROR CORE1 point Result = %d, retrying SEND ...\n",eRR);
			sleep(4);
		}
		else
		{
			printf("\t\t **** main(): CORE1 Point print function, X = %d, Y = %d\n",sP_out.x, sP_out.y);
		}
	}

	Result = 0;
	eRR = RPCERR_SEND_FAILED;
	while ( (eRR != RPCERR_SUCCESS) && ( eRR == RPCERR_SEND_FAILED))
	{
		sPoint spoint;
		spoint.x = 9;
		spoint.y= 8;
		sPoint sOut;
		int arr_out[4];
		int i = 0;

		eRR = fn_test_all (9, "TESTALL FUNCTION FROM MAIN", spoint, 4, &arr[0], &sOut , 4, &arr_out[0]);

		if (eRR != RPCERR_SUCCESS )
		{
			printf("ERROR CORE3 TESTALL = %d, retrying SEND ...\n",eRR);
			sleep(4);
		}
		else
		{
			printf("\t\t **** main(): CORE3 TEStALL function\n");
			printf("*************** structure o/p X= %d, Y = %d\n",sOut.x , sOut.y);
			printf("********** Array output = [");
			for (i=0; i<4; i++)
			{
				printf("%d ",arr_out[i]);
			}
			printf("]\n");
		}

	}

	Result = 0;
	eRR = RPCERR_SEND_FAILED;
	while ( (eRR != RPCERR_SUCCESS) && ( eRR == RPCERR_SEND_FAILED))
	{
		eRR = fn_get_rpc_version_core3(&Result);
		if (eRR != RPCERR_SUCCESS )
		{
			printf("\t\t!!!!!!!!! ERROR CORE3 Result = %d, retrying SEND...\n",eRR);
			sleep(4);
		}
		else
		{
			printf(" \t\t **** main(): CORE3 RESULT = %d\n",Result);
		}
	}
	Result = 0;
	eRR = RPCERR_SEND_FAILED;
	while ( (eRR != RPCERR_SUCCESS) && ( eRR == RPCERR_SEND_FAILED))
	{
		eRR = fn_array_core3 (4, &arr[0]);
		if (eRR != RPCERR_SUCCESS )
		{
			printf("ERROR CORE3 Array Result = %d, retrying SEND ...\n",eRR);
			sleep(4);
		}
		else
		{
			printf("\t\t **** main(): CORE3 Array function\n");
		}
	}

	eRR = fn_print_hello("HELLO , CORE3 PRINT FUNCTION ");
#endif
	rpc_prompt();   /* interactive: type a function name to call it */

	os_close_socket();
	os_net_cleanup();   /* WSACleanup on Windows (no-op on POSIX) */
	return eRR;

	return 0;
}
