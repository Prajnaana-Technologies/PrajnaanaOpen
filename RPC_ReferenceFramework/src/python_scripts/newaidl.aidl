

interface IElekService
 {
    IBinder fn_compute_mod_aidl (int x, IN int y, OUT int *pmod));
    IBinder fn_get_rpc_version_core1_aidl (OUT int *p_rpc_ver));
    IBinder fn_print_point_core1_aidl (IN sPoint sP, OUT sPoint *psP));
    IBinder fn_compute_sqr_aidl (IN int x, OUT int *psqr));
    IBinder fn_get_rpc_version_core2_aidl (OUT int *p_rpc_ver));
    IBinder fn_array_core3_aidl (IN RPCARR_SIZE int iSize, INOUT RPC_TYPE_ARRAY int *ai_Arr));
    IBinder fn_test_all_aidl (IN int x, IN RPCARR_SIZE int iSize_str, IN RPC_TYPE_ARRAY char *str, IN sPoint sP, IN RPCARR_SIZE int iSize, IN RPC_TYPE_ARRAY int *ai_arr, OUT sPoint *psOut, IN RPCARR_SIZE int outsize, OUT RPC_TYPE_ARRAY int *ai_out));
    IBinder fn_print_hello_aidl (IN RPCARR_SIZE int iSize, INOUT RPC_TYPE_ARRAY char *print_str));
    IBinder fn_get_rpc_version_core3_aidl (OUT int *p_rpc_ver));
}