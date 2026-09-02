# Copyright (c) 2026 Prajnaana Technologies
# SPDX-License-Identifier: MIT
#
# Part of the Multi-Core RPC Framework.
# See the LICENSE file in the project root for the full license text.
#
# Original Author: Mamatha BV

from C_Code_Generator import C_CodeBuilder 
import sys

if __name__ == "__main__":
    
    input_header = 'rpc_fn.h'
    
    code_obj = C_CodeBuilder()
    code_obj.get_fn_list(input_header)
    #From the input rpc_fn.h , create seperate list of functions for each core
    code_obj.print_list("Core Tags list = ",code_obj.Cores)
    code_obj.print_list("CORE1 Function list =",code_obj.core1_fn_list)
    code_obj.print_list("CORE2 Function list =",code_obj.core2_fn_list)
    code_obj.print_list("CORE3 Function list =",code_obj.core3_fn_list)
    
    #create rpc_fn_core.h file from the list of extracted filenames
    code_obj.create_fn_code("rpc_fncode.h",input_header)
    #create the server files for the core specified
    if not (len(code_obj.core1_fn_list)==0):
        code_obj.create_server_file('rpc_core1')
        code_obj.create_client_file('rpc_core1')
        
    if not (len(code_obj.core2_fn_list)==0):
        code_obj.create_server_file('rpc_core2')
        code_obj.create_client_file('rpc_core2')
        
    if not (len(code_obj.core3_fn_list)==0):
        code_obj.create_server_file('rpc_core3')
        code_obj.create_client_file('rpc_core3')
        
    #create the client files for the core specified
     # client file for core rpc_core1
     # client file for core rpc_core2
     # client file for core rpc_core3
    code_obj.create_aidl_file()
    for idx in range(code_obj.HASHPRIME):
        print(code_obj.fn_hashtable[idx])
        

