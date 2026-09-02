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
 * THIS IS THE FILE CONTAINING DEFINITIONS NEEDED FOR RPC MARSHALLING AND
 * DEMARSHALLING FUNCTIONS
 * ****************************************************************************/
#ifndef RPC_MARSHALL_H
#define RPC_MARSHALL_H

#include "rpc_fncode.h"
#include "os_platform.h"   /* OS-selected system headers (sockets etc.) */
#define FN_RESP_WAIT_TIMEOUT        (10000u)  /* Timeout in millisecs */
#define MAX_FN_NAME_SIZE            (128u)
#define RPCHDR_START_SIGN           (0x50A0050Au)
#define RPCHDR_END_SIGN             (0xAF5FFAF5u)
#define OS_SEM_TIMEOUT		    (100u)



enumFnCore get_this_core(void);
extern void* os_create_semaphore(void);
extern void* os_create_mutex(void); 
/* ****************************************************************************
 * MACRO to Initialize FN-TABLE entries
 * ****************************************************************************/
#define INIT_FN_TABLE(__fname__, __idx__, __srvrfn__, __srccore__, __has_output__) \
do {                                                                \
    if (NULL != __fname__) {                                        \
        strncpy (rpc_fn_table [__idx__].fn_name, __fname__, MAX_FN_NAME_SIZE); \
    }                                                               \
    rpc_fn_table [__idx__].pfn_server = __srvrfn__;                 \
    rpc_fn_table [__idx__].e_fn_core  = __srccore__;                \
    if ((__has_output__) && (NULL == __srvrfn__)) {                 \
        rpc_fn_table [__idx__].p_wait_sema = os_create_semaphore ();\
        rpc_fn_table [__idx__].p_fn_mutex  = os_create_mutex ();    \
    }\
} while (0)

/*****************************************************************************
* FUNCTION PROTOTYPE FOR THE SERVER FUNCTIONS
typedef INT32 (*cli_handler_ptr) (CHAR *pcBuf[] , CHAR * pcResponse);
*****************************************************************************/
typedef enumRpcErr (*RPC_SRVR_FN)( void* pSrc) ;

/* ****************************************************************************
 * ENUM used for response indications
 * ****************************************************************************/
 typedef enum
 {
    REQ_RESPONSE_NONE,
    REQ_RESPONSE_AWAITED,
    REQ_RESPONSE_RETURNED,

 } enumReqResponse;

/* ****************************************************************************
 * STRUCTURE used for passing function arguments
 * ****************************************************************************/
typedef struct  tagFnArgs
{
    enumArgDir      e_arg_type;     /* IN / OUT / INOUT */
    unsigned int    arg_len;
    unsigned int   *p_arg;

} structArg, *pstructArg;

/* ****************************************************************************
 * STRUCTURE used for passing RPC-Marshalling related codes in the buffer
 * ****************************************************************************/
typedef struct  tagMarshalPriv
{
    unsigned int    rpc_start_sign;

    unsigned int    pkt_size       : 16;
    unsigned int    rpc_version    : 16;

    int             rpc_return_err : 16;
    int             eReqResponse   :  4;    /* enumReqResponse */
    int             e_src_core     :  4;    /* enumFnCore */
    int             e_dst_core     :  4;    /* enumFnCore */
    int             reserved       :  4;

    char            fn_name [MAX_FN_NAME_SIZE];
    unsigned int    fn_rpc_instance;
    int    rpc_end_sign;
    int    rpc_hdr_checksum;

} structMarshall, *pstructMarshall;

/* ****************************************************************************
 * STRUCTURE to form function table
 * ****************************************************************************/
typedef struct  tagFnServer
{
    char            fn_name [MAX_FN_NAME_SIZE];
    RPC_SRVR_FN     pfn_server;
    enumFnCore      e_fn_core;    /* Target Core ID where the function is served */
    unsigned int    fn_rpc_instance;
    void           *p_resp_buf;
    void           *p_wait_sema;
    void           *p_fn_mutex;

} structFnInfo, *pstructFnInfo;

extern structFnInfo    rpc_fn_table [FNCODE_HASHPRIME];
/* ****************************************************************************
 * FUNCTIONS exported by Marshall/Demarshall modules
 * ****************************************************************************/
enumRpcErr  rpc_marshal (char *fn_name, enumRpcFnCode fn_code, unsigned int arg_count, structArg *ps_arg);
unsigned int    get_fn_code (char *fn_name);

/* ****************************************************************************
 * EXTERNS functions used by Marshall/Demarshall modules
 * ****************************************************************************/
extern  void            fn_table_init ();

/* ****************************************************************************
 * ****************************************************************************/
#endif  /* END RPC_MARSHALL_H */

/* ************************************************************************** *
 * ***************************** END OF FILE ******************************** */

