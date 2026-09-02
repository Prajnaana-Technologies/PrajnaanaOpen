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


static enumRpcErr fn_compute_mod_C (int x, IN int y, OUT int *pmod)
{
    structArg   sArg[3];
    enumRpcErr  eRpcErr;

    sArg[0].e_arg_type = RPCARG_IN;
    sArg[0].arg_len = sizeof(int) ;
    sArg[0].p_arg = (unsigned int*)&x;

    sArg[1].e_arg_type = RPCARG_IN;
    sArg[1].arg_len = sizeof(int) ;
    sArg[1].p_arg = (unsigned int*)&y;

    sArg[2].e_arg_type = RPCARG_OUT;
    sArg[2].arg_len = sizeof(int) ;
    sArg[2].p_arg = (unsigned int*)pmod;

    eRpcErr = rpc_marshal ( "fn_compute_mod_S" , FNCODE_FN_COMPUTE_MOD, 3, &(sArg[0]) );

    return (eRpcErr);
}
 enumRpcErr  fn_compute_mod (int x, IN int y, OUT int *pmod)
{
    return ( fn_compute_mod_C (x , y , pmod)  );
}

static enumRpcErr fn_get_rpc_version_core1_C (OUT int *p_rpc_ver)
{
    structArg   sArg[1];
    enumRpcErr  eRpcErr;

    sArg[0].e_arg_type = RPCARG_OUT;
    sArg[0].arg_len = sizeof(int) ;
    sArg[0].p_arg = (unsigned int*)p_rpc_ver;

    eRpcErr = rpc_marshal ( "fn_get_rpc_version_core1_S" , FNCODE_FN_GET_RPC_VERSION_CORE1, 1, &(sArg[0]) );

    return (eRpcErr);
}
 enumRpcErr  fn_get_rpc_version_core1 (OUT int *p_rpc_ver)
{
    return ( fn_get_rpc_version_core1_C (p_rpc_ver)  );
}

static enumRpcErr fn_print_point_core1_C (IN sPoint sP, OUT sPoint *psP)
{
    structArg   sArg[2];
    enumRpcErr  eRpcErr;

    sArg[0].e_arg_type = RPCARG_IN;
    sArg[0].arg_len = sizeof(sPoint) ;
    sArg[0].p_arg = (unsigned int*)&sP;

    sArg[1].e_arg_type = RPCARG_OUT;
    sArg[1].arg_len = sizeof(sPoint) ;
    sArg[1].p_arg = (unsigned int*)psP;

    eRpcErr = rpc_marshal ( "fn_print_point_core1_S" , FNCODE_FN_PRINT_POINT_CORE1, 2, &(sArg[0]) );

    return (eRpcErr);
}
 enumRpcErr  fn_print_point_core1 (IN sPoint sP, OUT sPoint *psP)
{
    return ( fn_print_point_core1_C (sP , psP)  );
}

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
