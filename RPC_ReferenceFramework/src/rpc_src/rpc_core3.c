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


/* print_str is INOUT: CORE3 prefixes "Hello " to the caller's string, prints the
 * result, and echoes it back in the SAME buffer. iSize is the TOTAL buffer size
 * (the caller sizes it with room for the "Hello " prefix + text + '\0'). */
enumRpcErr  fn_print_hello (IN RPCARR_SIZE int iSize, INOUT RPC_TYPE_ARRAY char *print_str)
{
    char temp[256];
    /* Ensure the incoming text is bounded/terminated before we read it. */
    if (iSize > 0) { print_str[iSize - 1] = '\0'; }
    snprintf (temp, sizeof(temp), "Hello %s", print_str);
    /* Write the prefixed result back into the caller's buffer (bounded by iSize). */
    strncpy (print_str, temp, iSize);
    if (iSize > 0) { print_str[iSize - 1] = '\0'; }
    printf ("%s\n", print_str);
    return (RPCERR_SUCCESS);
}

enumRpcErr  fn_get_rpc_version_core3 (OUT int *p_rpc_ver)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	*p_rpc_ver = 1.0;
	return eRpcErr;
}

/* ai_Arr is INOUT: it is printed here on CORE3 and echoed back UNCHANGED to the
 * caller (any core can invoke this and receive the same array back - like
 * fn_print_point_core1). "Take an array as input and print it back." */
enumRpcErr  fn_array_core3 (IN RPCARR_SIZE int iSize, INOUT RPC_TYPE_ARRAY int *ai_Arr)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	int i = 0;
	printf("~~~~~~~~~~~~~~~ CORE3 Array function: printing %d element(s):\n", iSize);
	for ( i = 0 ; i < iSize ; i++)
	{
		printf("  [%d] = %d\n", i, ai_Arr[i]);
	}
	printf("~~~~~~~~~~~~~~~ CORE3: echoing the array back to the caller\n");
	/* No modification = pure echo. (Change ai_Arr[i] here if you want CORE3 to
	 * transform it, e.g. ai_Arr[i] += 1, and the caller would see that.) */
	return eRpcErr;
}

enumRpcErr  fn_test_all (IN int x, IN RPCARR_SIZE int iSize_str, IN RPC_TYPE_ARRAY char *str, IN sPoint sP, IN RPCARR_SIZE int iSize, IN RPC_TYPE_ARRAY int *ai_arr, OUT sPoint *psOut, IN RPCARR_SIZE int outsize, OUT RPC_TYPE_ARRAY int *ai_out)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	int i = 0;

	printf("##############  TEST ALL FUNCTION #############\n");
	printf(" INT = %d\n",x);
	printf(" STRING = %s\n", str);
	printf(" STRUCT X = %d \t Y = %d\n",sP.x,sP.y);
	printf(" ARRAY PASSED = [ ");
	for ( i =0; i< iSize ; i++)
	{
		printf( "%d ",*ai_arr);
		*ai_out = (*ai_arr) + 1;
		ai_out++;
		ai_arr++;
	}
	printf("]\n");

	psOut->x = sP.x + 1;
	psOut->y = sP.y + 1;

	printf(" OUTPUT STRUCT = %d, %d\n",psOut->x,psOut->y);

	printf("]\n");

	return eRpcErr;
}


enumRpcErr  get_rtc (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *rtc)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  get_location (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *location)
{
	
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  get_parking_status (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *parking_status)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  get_speed (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *speed)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  get_fuel_status (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *fuel_status)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  get_CAN_info (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *can_info)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  get_gyro_accel (OUT RPCARR_SIZE int iSize_gyr ,OUT RPC_TYPE_ARRAY char *gyro, OUT RPCARR_SIZE int iSize_accel, OUT RPC_TYPE_ARRAY char *accel)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
enumRpcErr  get_ambient_light_value (OUT RPCARR_SIZE int iSize, OUT RPC_TYPE_ARRAY char *ambient_light)
{
	enumRpcErr  eRpcErr = RPCERR_SUCCESS;
	return eRpcErr;
}
