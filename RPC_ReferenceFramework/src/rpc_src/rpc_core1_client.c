/*
 * Copyright (c) 2026 Prajnaana Technologies
 * SPDX-License-Identifier: MIT
 *
 * Part of the Multi-Core RPC Framework.
 * See the LICENSE file in the project root for the full license text.
 *
 * Original Author: Mamatha BV
 */

/*****************************************************************************
* THIS IS AUTO-GENERATED CODE
* SHALL NOT BE MODIFIED MANUALLY; ALL CHANGES WILL BE LOST WHEN THE FILE IS
* AUTO-GENERATED AGAIN
*****************************************************************************/
#include "rpc_fncode.h"
#include "rpc_marshall.h"


static enumRpcErr fn_compute_sqr_C (IN int x, OUT int *psqr)
{
    structArg   sArg[2];
    enumRpcErr  eRpcErr;

    sArg[0].e_arg_type = RPCARG_IN;
    sArg[0].arg_len = sizeof(int) ;
    sArg[0].p_arg = (unsigned int*)&x;

    sArg[1].e_arg_type = RPCARG_OUT;
    sArg[1].arg_len = sizeof(int) ;
    sArg[1].p_arg = (unsigned int*)psqr;

    eRpcErr = rpc_marshal ( "fn_compute_sqr_S" , FNCODE_FN_COMPUTE_SQR, 2, &(sArg[0]) );

    return (eRpcErr);
}
 enumRpcErr  fn_compute_sqr (IN int x, OUT int *psqr)
{
    return ( fn_compute_sqr_C (x , psqr)  );
}

static enumRpcErr fn_get_rpc_version_core2_C (OUT int *p_rpc_ver)
{
    structArg   sArg[1];
    enumRpcErr  eRpcErr;

    sArg[0].e_arg_type = RPCARG_OUT;
    sArg[0].arg_len = sizeof(int) ;
    sArg[0].p_arg = (unsigned int*)p_rpc_ver;

    eRpcErr = rpc_marshal ( "fn_get_rpc_version_core2_S" , FNCODE_FN_GET_RPC_VERSION_CORE2, 1, &(sArg[0]) );

    return (eRpcErr);
}
 enumRpcErr  fn_get_rpc_version_core2 (OUT int *p_rpc_ver)
{
    return ( fn_get_rpc_version_core2_C (p_rpc_ver)  );
}

static enumRpcErr fn_array_core3_C (IN RPCARR_SIZE int iSize, INOUT RPC_TYPE_ARRAY int *ai_Arr)
{
    structArg   sArg[2];
    enumRpcErr  eRpcErr;

    sArg[0].e_arg_type = RPCARG_IN;
    sArg[0].arg_len = sizeof(int);
    sArg[0].p_arg = (unsigned int*)&iSize;

    sArg[1].e_arg_type = RPCARG_INOUT;
    sArg[1].arg_len = (*sArg[0].p_arg) *( sizeof(int) );
    sArg[1].p_arg = (unsigned int*)ai_Arr;

    eRpcErr = rpc_marshal ( "fn_array_core3_S" , FNCODE_FN_ARRAY_CORE3, 2, &(sArg[0]) );

    return (eRpcErr);
}
 enumRpcErr  fn_array_core3 (IN RPCARR_SIZE int iSize, INOUT RPC_TYPE_ARRAY int *ai_Arr)
{
    return ( fn_array_core3_C (iSize , ai_Arr)  );
}

static enumRpcErr fn_test_all_C (IN int x, IN RPCARR_SIZE int iSize_str, IN RPC_TYPE_ARRAY char *str, IN sPoint sP, IN RPCARR_SIZE int iSize, IN RPC_TYPE_ARRAY int *ai_arr, OUT sPoint *psOut, IN RPCARR_SIZE int outsize, OUT RPC_TYPE_ARRAY int *ai_out)
{
    structArg   sArg[9];
    enumRpcErr  eRpcErr;

    sArg[0].e_arg_type = RPCARG_IN;
    sArg[0].arg_len = sizeof(int) ;
    sArg[0].p_arg = (unsigned int*)&x;

    sArg[1].e_arg_type = RPCARG_IN;
    sArg[1].arg_len = sizeof(int);
    sArg[1].p_arg = (unsigned int*)&iSize_str;

    sArg[2].e_arg_type = RPCARG_IN;
    sArg[2].arg_len = (*sArg[1].p_arg) *( sizeof(char) );
    sArg[2].p_arg = (unsigned int*)str;

    sArg[3].e_arg_type = RPCARG_IN;
    sArg[3].arg_len = sizeof(sPoint) ;
    sArg[3].p_arg = (unsigned int*)&sP;

    sArg[4].e_arg_type = RPCARG_IN;
    sArg[4].arg_len = sizeof(int);
    sArg[4].p_arg = (unsigned int*)&iSize;

    sArg[5].e_arg_type = RPCARG_IN;
    sArg[5].arg_len = (*sArg[4].p_arg) *( sizeof(int) );
    sArg[5].p_arg = (unsigned int*)ai_arr;

    sArg[6].e_arg_type = RPCARG_OUT;
    sArg[6].arg_len = sizeof(sPoint) ;
    sArg[6].p_arg = (unsigned int*)psOut;

    sArg[7].e_arg_type = RPCARG_IN;
    sArg[7].arg_len = sizeof(int);
    sArg[7].p_arg = (unsigned int*)&outsize;

    sArg[8].e_arg_type = RPCARG_OUT;
    sArg[8].arg_len = (*sArg[7].p_arg) *( sizeof(int) );
    sArg[8].p_arg = (unsigned int*)ai_out;

    eRpcErr = rpc_marshal ( "fn_test_all_S" , FNCODE_FN_TEST_ALL, 9, &(sArg[0]) );

    return (eRpcErr);
}
 enumRpcErr  fn_test_all (IN int x, IN RPCARR_SIZE int iSize_str, IN RPC_TYPE_ARRAY char *str, IN sPoint sP, IN RPCARR_SIZE int iSize, IN RPC_TYPE_ARRAY int *ai_arr, OUT sPoint *psOut, IN RPCARR_SIZE int outsize, OUT RPC_TYPE_ARRAY int *ai_out)
{
    return ( fn_test_all_C (x , iSize_str , str , sP , iSize , ai_arr , psOut , outsize , ai_out)  );
}

static enumRpcErr fn_print_hello_C (IN RPCARR_SIZE int iSize, INOUT RPC_TYPE_ARRAY char *print_str)
{
    structArg   sArg[2];
    enumRpcErr  eRpcErr;

    sArg[0].e_arg_type = RPCARG_IN;
    sArg[0].arg_len = sizeof(int);
    sArg[0].p_arg = (unsigned int*)&iSize;

    sArg[1].e_arg_type = RPCARG_INOUT;
    sArg[1].arg_len = (*sArg[0].p_arg) *( sizeof(char) );
    sArg[1].p_arg = (unsigned int*)print_str;

    eRpcErr = rpc_marshal ( "fn_print_hello_S" , FNCODE_FN_PRINT_HELLO, 2, &(sArg[0]) );

    return (eRpcErr);
}
 enumRpcErr  fn_print_hello (IN RPCARR_SIZE int iSize, INOUT RPC_TYPE_ARRAY char *print_str)
{
    return ( fn_print_hello_C (iSize , print_str)  );
}

static enumRpcErr fn_get_rpc_version_core3_C (OUT int *p_rpc_ver)
{
    structArg   sArg[1];
    enumRpcErr  eRpcErr;

    sArg[0].e_arg_type = RPCARG_OUT;
    sArg[0].arg_len = sizeof(int) ;
    sArg[0].p_arg = (unsigned int*)p_rpc_ver;

    eRpcErr = rpc_marshal ( "fn_get_rpc_version_core3_S" , FNCODE_FN_GET_RPC_VERSION_CORE3, 1, &(sArg[0]) );

    return (eRpcErr);
}
 enumRpcErr  fn_get_rpc_version_core3 (OUT int *p_rpc_ver)
{
    return ( fn_get_rpc_version_core3_C (p_rpc_ver)  );
}
