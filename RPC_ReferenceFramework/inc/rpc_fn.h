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
 * THIS IS THE FILE CONTAINING PROTOTYPES OF ALL THE RPC FUNCTIONS,
 * IRRESPECTIVE OF THE CORE ON WHICH THE TARGET FUNTION IS REALIZED.
 * THIS FILE IS USED TO AUTO-GENERATE CLIENT AND SERVICE FILES FOR
 * EACH OF THE CORES.
 * ****************************************************************************/
#ifndef RPC_FN_H
#define RPC_FN_H
#include <stddef.h>
#include <stdio.h>
#include <string.h>

/* ****************************************************************************
 * TYPEDEFs used for RPC argument types
 * ****************************************************************************/


/* ****************************************************************************
 * #DEFINEs to specify CORE on which a function is actually implemented
 * ****************************************************************************/
#define RPCFN_CORE1
#define RPCFN_CORE2
#define RPCFN_CORE3
#define RPCARR_SIZE
#define RPC_TYPE_ARRAY

/* ****************************************************************************
 * #DEFINEs to specify direction of Arguments for a given function
 *      IN      ==> Argument is input to the function
 *      OUT     ==> Argument is output from the function
 *      INOUT   ==> This arguemnt is both IN and OUT for the function
 * ****************************************************************************/
#define IN
#define OUT
#define INOUT

/* ****************************************************************************
 * #ENUM to define ERRORS associated with invoking RPC
 * ****************************************************************************/
typedef enum
{
    RPCERR_SUCCESS,
    RPCERR_VERSION_MISMATCH,
    RPCERR_SEND_FAILED,
    RPCERR_INVALID_ARG,
    RPCERR_SEND_BUF_FULL,
    RPCERR_MALLOC_FAILED,
    RPCERR_RESPONSE_TIMEOUT,
    RPCERR_UNSUPPORTED_FUNCTION,
    RPCERR_MAX,

} enumRpcErr;

typedef struct structPoint
{
    int x;
    int y;
}sPoint, *psPoint;

/* ****************************************************************************
 * Prototypes of the RPC functions
 * ****************************************************************************/
RPCFN_CORE1  enumRpcErr  fn_compute_mod (int x, IN int y, OUT int *pmod);
RPCFN_CORE2  enumRpcErr  fn_compute_sqr (IN int x, OUT int *psqr);
RPCFN_CORE1  enumRpcErr  fn_get_rpc_version_core1 (OUT int *p_rpc_ver);
RPCFN_CORE2  enumRpcErr  fn_get_rpc_version_core2 (OUT int *p_rpc_ver);
RPCFN_CORE1  enumRpcErr  fn_print_point_core1 (IN sPoint sP, OUT sPoint *psP);
RPCFN_CORE3  enumRpcErr  fn_array_core3 (IN RPCARR_SIZE int iSize, INOUT RPC_TYPE_ARRAY int *ai_Arr);
RPCFN_CORE3  enumRpcErr  fn_test_all (IN int x, IN RPCARR_SIZE int iSize_str, IN RPC_TYPE_ARRAY char *str, IN sPoint sP, IN RPCARR_SIZE int iSize, IN RPC_TYPE_ARRAY int *ai_arr, OUT sPoint *psOut, IN RPCARR_SIZE int outsize, OUT RPC_TYPE_ARRAY int *ai_out);
RPCFN_CORE3  enumRpcErr  fn_print_hello (IN RPCARR_SIZE int iSize, INOUT RPC_TYPE_ARRAY char *print_str);
RPCFN_CORE3  enumRpcErr  fn_get_rpc_version_core3 (OUT int *p_rpc_ver);

int get_hash_index ( char* pc_fnName );
/* ****************************************************************************
 * ****************************************************************************/
#endif  /* END RPC_FN_H */

/* ************************************************************************** *
 * ***************************** END OF FILE ******************************** */

