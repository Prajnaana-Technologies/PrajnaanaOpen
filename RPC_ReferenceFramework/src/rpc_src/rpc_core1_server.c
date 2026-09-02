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


enumRpcErr fn_compute_mod_S ( void *p_param)
{
    structArg       sArg[3];
    unsigned char  *p_data  = (unsigned char *)p_param;
    enumRpcErr      eRpcErr = RPCERR_SUCCESS;

    if ( NULL != p_param )
    {
        sArg[0].e_arg_type = RPCARG_IN;
        sArg[0].arg_len = sizeof(int) ;
        sArg[0].p_arg = (unsigned int*)p_data;
        p_data += sArg[0].arg_len;

        sArg[1].e_arg_type = RPCARG_IN;
        sArg[1].arg_len = sizeof(int) ;
        sArg[1].p_arg = (unsigned int*)p_data;
        p_data += sArg[1].arg_len;

        sArg[2].e_arg_type = RPCARG_OUT;
        sArg[2].arg_len = sizeof(int) ;
        sArg[2].p_arg = (unsigned int*)p_data;
        p_data += sArg[2].arg_len;

        eRpcErr = fn_compute_mod(*( (int*)(sArg[0].p_arg) ),
        *( (int*)(sArg[1].p_arg) ),
        (int*)(sArg[2].p_arg));

    }
    return (eRpcErr);

}
enumRpcErr fn_get_rpc_version_core1_S ( void *p_param)
{
    structArg       sArg[1];
    unsigned char  *p_data  = (unsigned char *)p_param;
    enumRpcErr      eRpcErr = RPCERR_SUCCESS;

    if ( NULL != p_param )
    {
        sArg[0].e_arg_type = RPCARG_OUT;
        sArg[0].arg_len = sizeof(int) ;
        sArg[0].p_arg = (unsigned int*)p_data;
        p_data += sArg[0].arg_len;

        eRpcErr = fn_get_rpc_version_core1((int*)(sArg[0].p_arg));

    }
    return (eRpcErr);

}
enumRpcErr fn_print_point_core1_S ( void *p_param)
{
    structArg       sArg[2];
    unsigned char  *p_data  = (unsigned char *)p_param;
    enumRpcErr      eRpcErr = RPCERR_SUCCESS;

    if ( NULL != p_param )
    {
        sArg[0].e_arg_type = RPCARG_IN;
        sArg[0].arg_len = sizeof(sPoint) ;
        sArg[0].p_arg = (unsigned int*)p_data;
        p_data += sArg[0].arg_len;

        sArg[1].e_arg_type = RPCARG_OUT;
        sArg[1].arg_len = sizeof(sPoint) ;
        sArg[1].p_arg = (unsigned int*)p_data;
        p_data += sArg[1].arg_len;

        eRpcErr = fn_print_point_core1(*( (sPoint*)(sArg[0].p_arg) ),
        (sPoint*)(sArg[1].p_arg));

    }
    return (eRpcErr);

}

enumFnCore  get_this_core ( void )
{
    return (RPCCORE_CORE1);
}

void    fn_table_init ( void )
{
    INIT_FN_TABLE( "fn_compute_mod_S" , FNCODE_FN_COMPUTE_MOD , fn_compute_mod_S , RPCCORE_CORE1 , 1);
    INIT_FN_TABLE( "fn_get_rpc_version_core1_S" , FNCODE_FN_GET_RPC_VERSION_CORE1 , fn_get_rpc_version_core1_S , RPCCORE_CORE1 , 1);
    INIT_FN_TABLE( "fn_print_point_core1_S" , FNCODE_FN_PRINT_POINT_CORE1 , fn_print_point_core1_S , RPCCORE_CORE1 , 1);
    INIT_FN_TABLE( "fn_compute_sqr_S" , FNCODE_FN_COMPUTE_SQR , NULL , RPCCORE_CORE2 , 1);
    INIT_FN_TABLE( "fn_get_rpc_version_core2_S" , FNCODE_FN_GET_RPC_VERSION_CORE2 , NULL , RPCCORE_CORE2 , 1);
    INIT_FN_TABLE( "fn_array_core3_S" , FNCODE_FN_ARRAY_CORE3 , NULL , RPCCORE_CORE3 , 1);
    INIT_FN_TABLE( "fn_test_all_S" , FNCODE_FN_TEST_ALL , NULL , RPCCORE_CORE3 , 1);
    INIT_FN_TABLE( "fn_print_hello_S" , FNCODE_FN_PRINT_HELLO , NULL , RPCCORE_CORE3 , 1);
    INIT_FN_TABLE( "fn_get_rpc_version_core3_S" , FNCODE_FN_GET_RPC_VERSION_CORE3 , NULL , RPCCORE_CORE3 , 1);
    return;
}


 int get_hash_index ( char* pc_fnName )
{
    int iTotal = 0;

    while ( *pc_fnName )
    {
        iTotal ^= *pc_fnName++ ; 
    }

    iTotal = iTotal % FNCODE_HASHPRIME ;
    return ( iTotal );
}
