/*
 * Copyright (c) 2026 Prajnaana Technologies
 * SPDX-License-Identifier: MIT
 *
 * Part of the Multi-Core RPC Framework.
 * See the LICENSE file in the project root for the full license text.
 *
 * Original Author: Mamatha BV
 */

#include "rpc_fn.h"
enumRpcErr  fn_compute_mod (IN int x, IN int y, OUT int *pmod)
{
    enumRpcErr  eRpcErr = RPCERR_SUCCESS;
    *pmod = 0;
    if (0 != y)
    {
        *pmod = (x % y);
        printf ("X=%d Y=%d MOD=%d\n", x, y, (x % y));
    }
    else
    {
        eRpcErr = RPCERR_INVALID_ARG;
        printf ("!! Error !! X=%d Y=%d\n", x,y);
    }
    return (eRpcErr);
}

enumRpcErr  fn_get_rpc_version_core1 (OUT int *p_rpc_ver)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	*p_rpc_ver = 33;
	return eRpcErr;
}

enumRpcErr  fn_print_point_core1 (IN sPoint sP, OUT sPoint *psP)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	printf("\n\n@@@@@@@@@@@@@@@@@@@@ Input : Point X value = %d,Point Y value = %d\n\n",
			sP.x , sP.y);
	psP->x = sP.x + 1;
	psP->y = sP.y + 1;
	printf("\n\n################### OUTPUT : Point  X Val = %d, Point Y Val = %d\n\n",
			psP->x , psP->y);

	return eRpcErr;

}

enumRpcErr  AA_connect_event (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *connect_AA)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  AA_disconnect_event (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *disconnect_AA)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  CP_connect_event (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *connect_CP)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  CP_disconnect_event (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *disconnect_CP)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  Notify_CP_error (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *CP_Error)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  Notify_AA_error (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *AA_Error)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;

}
enumRpcErr  Notify_android_ready (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *ISAndroid_Ready)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  HeartBeat_CORE1 (void)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
