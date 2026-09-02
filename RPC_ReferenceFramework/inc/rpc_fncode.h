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

#ifndef RPC_FNCODE
#define RPC_FNCODE

#include "rpc_fn.h"

/*****************************************************************************
* #DEFINE to declare the RPC Protocol Version Number
* !! THIS SHALL BE UPDATED WHENEVER THE RPC FUNCTION PROTOTYPES ARE MODIFIED !!
*****************************************************************************/
#define RPC_VERSION         0x0001

/*****************************************************************************
* #ENUM that assigns values to the direction of Arguments
*****************************************************************************/
typedef enum
{
    RPCARG_IN,
    RPCARG_OUT,
    RPCARG_INOUT,
    RPCARG_MAX = 0xFFFFFFFFu,   /* To make the ENUM a 32-bit field */
} enumArgDir;

/*****************************************************************************
* #ENUM that assigns values to the Cores
*****************************************************************************/
typedef enum
{
    RPCCORE_CORE1,
    RPCCORE_CORE2,
    RPCCORE_CORE3,
    RPCCORE_COUNT,
	 RPCCCORE_MAX = 0xFFFFFFFFu,   /* To make the ENUM a 32-bit field */
} enumFnCore;

/*****************************************************************************
* ENUM to assign CODES for each of the RPC functions
*****************************************************************************/

typedef enum 
{
   FNCODE_FN_COMPUTE_MOD = 4 ,
   FNCODE_FN_GET_RPC_VERSION_CORE1 = 10 ,
   FNCODE_FN_PRINT_POINT_CORE1 = 13 ,
   FNCODE_FN_COMPUTE_SQR = 1 ,
   FNCODE_FN_GET_RPC_VERSION_CORE2 = 7 ,
   FNCODE_FN_ARRAY_CORE3 = 9 ,
   FNCODE_FN_TEST_ALL = 2 ,
   FNCODE_FN_PRINT_HELLO = 5 ,
   FNCODE_FN_GET_RPC_VERSION_CORE3 = 8 ,
   FNCODE_HASHPRIME = 19

}enumRpcFnCode;

#endif  /* RPC_FNCODE */