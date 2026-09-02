/*
 * Copyright (c) 2026 Prajnaana Technologies
 * SPDX-License-Identifier: MIT
 *
 * Part of the Multi-Core RPC Framework.
 * See the LICENSE file in the project root for the full license text.
 *
 * Original Author: Mamatha BV
 */

/* ****************************************************************************
 * CROSS-PLATFORM ABSTRACTION HEADER
 * Selects the correct system headers, socket / thread / sleep primitives for
 * the target OS at compile time. Include this instead of the raw POSIX or
 * Winsock headers so the rest of the code stays OS-agnostic.
 *
 *   Windows : Winsock2 + Win32 threads/semaphores/critical-sections
 *   POSIX   : BSD sockets + pthreads + POSIX semaphores
 * ****************************************************************************/
#ifndef OS_PLATFORM_H
#define OS_PLATFORM_H

/* -------- Common C-library headers (portable everywhere) ---------------- */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <stdint.h>
#include <limits.h>
#include <time.h>

#if defined(_WIN32) || defined(_WIN64)
/* ============================ WINDOWS ================================== */
    #ifndef WIN32_LEAN_AND_MEAN
    #define WIN32_LEAN_AND_MEAN
    #endif
    #include <winsock2.h>   /* MUST be included before <windows.h>        */
    #include <ws2tcpip.h>   /* socklen_t, inet_pton, modern name lookups  */
    #include <windows.h>    /* threads, semaphores, critical sections     */

    #ifdef _MSC_VER
        #pragma comment(lib, "ws2_32.lib")  /* auto-link Winsock on MSVC  */
    #endif

    typedef SOCKET  os_socket_t;
    #define OS_INVALID_SOCKET   INVALID_SOCKET

    /* Sockets on Windows are not file descriptors: use recv/send/closesocket */
    #define os_read(fd, buf, len)   recv((fd), (char *)(buf), (int)(len), 0)
    #define os_write(fd, buf, len)  send((fd), (const char *)(buf), (int)(len), 0)
    #define os_close_fd(fd)         closesocket(fd)

    #define os_sleep_sec(s)         Sleep((DWORD)(s) * 1000u)
    #define os_sleep_ms(ms)         Sleep((DWORD)(ms))

#else
/* ============================= POSIX ================================== */
    #include <sys/types.h>
    #include <sys/socket.h>
    #include <netinet/in.h>
    #include <arpa/inet.h>          /* inet_addr(), htons()               */
    #include <netdb.h>
    #include <unistd.h>             /* read/write/close, sleep()          */
    #include <pthread.h>
    #include <semaphore.h>

    typedef int  os_socket_t;
    #define OS_INVALID_SOCKET   (-1)

    #define os_read(fd, buf, len)   read((fd), (buf), (len))
    #define os_write(fd, buf, len)  write((fd), (buf), (len))
    #define os_close_fd(fd)         close(fd)

    #define os_sleep_sec(s)         sleep((unsigned int)(s))
    #define os_sleep_ms(ms)         usleep((useconds_t)(ms) * 1000u)

#endif

#endif  /* OS_PLATFORM_H */
