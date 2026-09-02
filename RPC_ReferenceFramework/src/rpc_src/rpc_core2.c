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
enumRpcErr  fn_compute_sqr (IN int x, OUT int *psqr)
{
    *psqr = (x * x);
    printf ("X=%d X^2=%d\n", x, (x * x));
    return (RPCERR_SUCCESS);
}

enumRpcErr  fn_get_rpc_version_core2 (OUT int *p_rpc_ver)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	*p_rpc_ver = 22.0;
	return eRpcErr;
}

enumRpcErr  HeartBeat_CORE2 (void)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
