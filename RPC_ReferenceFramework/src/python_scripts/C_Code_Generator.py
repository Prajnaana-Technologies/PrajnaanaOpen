# Copyright (c) 2026 Prajnaana Technologies
# SPDX-License-Identifier: MIT
#
# Part of the Multi-Core RPC Framework.
# See the LICENSE file in the project root for the full license text.
#
# Original Author: Mamatha BV

import re
import os
class C_CodeBuilder:
    """
    ****************************************************************
    Helper function to create the C code files
    
    ****************************************************************
    """
    def  __init__ ( self ) :
        self.core1_fn_list=[]
        self.core2_fn_list=[]
        self.core3_fn_list=[]
        self.Cores = list()
        self.fn_code_dict={}
        self.hashentry = {}
        self.HASHPRIME = 0
        self.TotalNoFn = 0
        self.corenames= ["rpc_core1","rpc_core2","rpc_core3"]
        self.enumArgDir = {'IN':'RPCARG_IN',
                           'OUT':'RPCARG_OUT',
                           'INOUT':'RPCARG_INOUT',
                           'MAX':'RPCARG_MAX'}
        self.enumFnCore = {'rpc_core1' 	:'RPCCORE_CORE1',
                           'rpc_core2'	:'RPCCORE_CORE2',
                           'rpc_core3'	:'RPCCORE_CORE3',
                           ''			:'RPCCCORE_MAX'}
        self.fn_hashtable = [ [] ]
        #self.fn_hashtable  =[[] for i in range(self.TotalNoFn)]

        self.enumRpcFnCode = {}
        self.fn_native = {}
        # ---- Project layout (resolved from this script's location, so the
        # generator works regardless of the current working directory) --------
        #   headers -> inc/ , generated C -> src/rpc_src/
        _script_dir = os.path.dirname(os.path.abspath(__file__))       # src/python_scripts
        _repo_root  = os.path.dirname(os.path.dirname(_script_dir))     # repo root
        self.script_dir = _script_dir
        self.inc_dir = os.path.join(_repo_root, 'inc')
        self.src_dir = os.path.join(_repo_root, 'src', 'rpc_src')
    """
    ****************************************************************
    Function to find whether the number is prime or not

    INPUT : 1. Number
    OUTPUT: 1.True : if the number is prime, else False
    ****************************************************************
    """
    def is_prime ( self, number ):
        if number > 1:
            for num in range(2, number):
                if number % num == 0:
                    return False
            return True
        return False
    """
    ****************************************************************
    Function to find the prime number next to the hash size

    INPUT : 1. Number
    OUTPUT: HASHPRIME variable should get updated to the next prime num
    ****************************************************************
    """
    def find_hashprime ( self, num ):
        num = num + num
        Prime = False
        while(not Prime):
            num = num + 1
            if self.is_prime( num ):
                Prime = True
                self.HASHPRIME = num
                print ( "HASHPRIME = {}\n".format(self.HASHPRIME))

    """
    ****************************************************************
    Function to update the hash table

    INPUT : 1. Index, to which the values are to be copied to
            2. Function name
            3. Function code
    If the  index is occupied, then the next index if checked if its
    empty and then the key,value pair is updated at that updated index
    ****************************************************************
    """
    def update_hashtable ( self, idx , fn_name , fn_code):

        if ( idx <  self.HASHPRIME ):

            if len(self.fn_hashtable[idx]) == 0:
                self.fn_hashtable[idx] = ((fn_name, fn_code))
                return idx
            else:
                idx = idx+1
                idx = self.update_hashtable(idx , fn_name , fn_code)

        else:
            idx = 0
            idx = self.update_hashtable(idx , fn_name , fn_code)


        return idx

    """
    ****************************************************************
    Function to convert the string to an integer value

    INPUT : 1. String to find the hash function
    OUTPUT: 1. Integer value calculated by xoring the ASCII of all the char in
            the string and then performing mod operation with prime number
    ****************************************************************
    """
    def hash_function ( self, hash_str ):
        hash_val = 0
        for n in range(len(hash_str)):
            hash_val = hash_val ^ ord(hash_str[n])
        hash_val = hash_val % self.HASHPRIME
        print('{} has HashVal = {}\n'.format(hash_str,hash_val))
        return hash_val


    """
    ****************************************************************
    Function to print the contents of a list

    INPUT : 1. String to print the name of the list
            2. List from which the string to be printed
    OUTPUT : NULL
    ****************************************************************
    """
    def print_list ( self , name , value_list ) :
        print("\n"+name+"\r")
        for x_1 in range(len(value_list)):
            print(value_list[x_1]+"\r")

    """
    ****************************************************************
    Function to get the list of functions for each of the cores

    INPUT : Filename which has the function prototypes
    OUTPUT : NULL
    ****************************************************************
    """
    def get_fn_list ( self , filename ) :
        inFile = open(os.path.join(self.inc_dir, filename),'r')
        read_content = inFile.read()
        read_content = read_content.splitlines()

        """
        Create a list of tags available for different cores
        and the list of functions for each core
        """
        for x in range(len(read_content)):
            line = read_content[x]

            if "#define" in line and "RPCFN" in line:
                self.Cores.append(line.split()[1])

            if ( len(self.Cores) == 3 ):

                if self.Cores[0] in line:
                    self.core1_fn_list.append(line.split(' ',1)[1])
                    self.fn_native[line.split(' ',1)[1]]='rpc_core1'
                    self.TotalNoFn = self.TotalNoFn + 1

                if self.Cores[1] in line:
                    self.core2_fn_list.append(line.split(' ',1)[1])
                    self.fn_native[line.split(' ',1)[1]]='rpc_core2'
                    self.TotalNoFn = self.TotalNoFn + 1

                if ( self.Cores[2] in line) and ( "#define" not in line) :
                    self.core3_fn_list.append(line.split(' ',1)[1])
                    self.fn_native[line.split(' ',1)[1]]='rpc_core3'
                    self.TotalNoFn = self.TotalNoFn + 1

        #Update the hash function table
        print("No. of Functions = {}\n".format(self.TotalNoFn))
        #Find the prime number next to total number of functions, this will be
        # hash size
        self.find_hashprime(self.TotalNoFn)
        #update the size of hash table list
        self.fn_hashtable  =[[] for i in range(self.HASHPRIME)]

        inFile.close()

    """
    ****************************************************************
    Function to write the data into the file
    INPUT : 1. File object of the file opened in write mode
            2. string to be written into the file
    ****************************************************************
    """
    def write ( self, file_obj , line : str  = ""):
        file_obj.writelines('{}'.format(line) )

    """
    ****************************************************************
    Function to write comments in the code file

    INPUT : 1. The object of the file opened in write mode
            2. Comment string
    ****************************************************************
    """
    def comment ( self , file_obj , comment : str):
        comment_str = "\n/*"+comment+"*/\n"
        self.write(file_obj , comment_str )

    """
    ****************************************************************
    Function which writes the File header comment to the created
    code file

    INPUT : The file object of the file opened in write mode
    ****************************************************************
    """
    def fHeader_Comment ( self , file_obj ):
        # MIT copyright banner, kept in sync with the hand-written sources so a
        # regenerated file still carries the license header.
        copyright_banner = "/*\n"\
                    " * Copyright (c) 2026 Prajnaana Technologies\n"\
                    " * SPDX-License-Identifier: MIT\n"\
                    " *\n"\
                    " * Part of the Multi-Core RPC Framework.\n"\
                    " * See the LICENSE file in the project root for the full license text.\n"\
                    " *\n"\
                    " * Original Author: Mamatha BV\n"\
                    " */\n"
        self.write ( file_obj , copyright_banner )
        H_comment = "****************************************************************************\n"\
                    "* THIS IS AUTO-GENERATED CODE\n"\
                    "* SHALL NOT BE MODIFIED MANUALLY; ALL CHANGES WILL BE LOST WHEN THE FILE IS\n"\
                    "* AUTO-GENERATED AGAIN\n"\
                    "****************************************************************************"
        self.comment ( file_obj , H_comment )

    """
    ****************************************************************
    Function which writes the File header comment to the created
    code file

    INPUT : The file object of the file opened in write mode
    ****************************************************************
    """
    def fn_code_write ( self , file_obj , filename ):

        self.write(file_obj,"\n#ifndef RPC_FNCODE\n")
        self.write(file_obj,'#define RPC_FNCODE\n\n')
        self.write(file_obj,'#include \"'+filename+'\"\n\n')

        self.write(file_obj, '/*****************************************************************************\n'
                             '* #DEFINE to declare the RPC Protocol Version Number\n'
                             '* !! THIS SHALL BE UPDATED WHENEVER THE RPC FUNCTION PROTOTYPES ARE MODIFIED !!\n'
                             '*****************************************************************************/\n')
        self.write(file_obj,'#define RPC_VERSION         0x0001\n\n')



        self.write(file_obj, '/*****************************************************************************\n'
                             '* #ENUM that assigns values to the direction of Arguments\n'
                             '*****************************************************************************/\n')
        self.write(file_obj, 'typedef enum\n{\n'
                                '    RPCARG_IN,\n    RPCARG_OUT,\n'
                                '    RPCARG_INOUT,\n    RPCARG_MAX = 0xFFFFFFFFu,   /* To make the ENUM a 32-bit field */\n'
                                '} enumArgDir;\n\n')

        self.write(file_obj, '/*****************************************************************************\n'
                             '* #ENUM that assigns values to the Cores\n'
                             '*****************************************************************************/\n')
        self.write(file_obj, 'typedef enum\n{\n'
                                '    RPCCORE_CORE1,\n    RPCCORE_CORE2,\n'
                                '    RPCCORE_CORE3,\n    RPCCORE_COUNT,\n'
                                '	 RPCCCORE_MAX = 0xFFFFFFFFu,   /* To make the ENUM a 32-bit field */\n'
                                '} enumFnCore;\n\n')

        self.write(file_obj, '/*****************************************************************************\n'
                             '* ENUM to assign CODES for each of the RPC functions\n'
                             '*****************************************************************************/\n')
        self.write(file_obj,'\ntypedef enum \n')
        self.write(file_obj,'{\n')


    """
    ****************************************************************
    Function to generate the rpc_fn_code.h file

    INPUT : Name of the file , along with the path to open it in
            write mode
    ****************************************************************
    """
    def create_fn_code ( self , filename , h_file ) :

        rpc_fn_code = open(os.path.join(self.inc_dir, filename),'w+')
        self.fHeader_Comment(rpc_fn_code)
        self.fn_code_write ( rpc_fn_code , h_file )

        for i in range(0,len(self.core1_fn_list)):
            str_enum = self.core1_fn_list[i].split('(')[0]
            str_enum = str_enum.split()[1]
            idx = self.hash_function (str_enum+'_S')

            fn_code = "FNCODE_"+str_enum.upper()
            idx = self.update_hashtable( idx , str_enum+'_S' , fn_code )
            self.enumRpcFnCode[str_enum] = fn_code
            self.write(rpc_fn_code,'   '+fn_code+' = '+str(idx)+' ,\n')
            self.fn_code_dict[str_enum] = fn_code

            print('{} stored in idx {}\n'.format(str_enum,idx))

        for i in range(0,len(self.core2_fn_list)):
            str_enum = self.core2_fn_list[i].split('(')[0]
            str_enum = str_enum.split()[1]
            idx = self.hash_function (str_enum+'_S')

            fn_code = "FNCODE_"+str_enum.upper()
            idx = self.update_hashtable( idx , str_enum+'_S' , fn_code )

            self.enumRpcFnCode[str_enum] = fn_code
            self.write(rpc_fn_code,'   '+fn_code+' = '+str(idx)+ ' ,\n')
            self.fn_code_dict[str_enum] = fn_code

            print('{} stored in idx {}\n'.format(str_enum,idx))

        for i in range(0,len(self.core3_fn_list)):
            str_enum = self.core3_fn_list[i].split('(')[0]
            str_enum = str_enum.split()[1]
            idx = self.hash_function (str_enum+'_S')

            fn_code = "FNCODE_"+str_enum.upper()
            idx = self.update_hashtable( idx , str_enum+'_S' , fn_code )
            self.enumRpcFnCode[str_enum] = fn_code
            self.write(rpc_fn_code,'   '+fn_code+' = '+str(idx)+' ,\n')
            self.fn_code_dict[str_enum] = fn_code

            print('{}_S stored in idx {}\n'.format(str_enum,idx))

        self.write(rpc_fn_code,'   FNCODE_HASHPRIME = '+str(self.HASHPRIME)+'\n\n')
        self.write(rpc_fn_code,'}enumRpcFnCode;\n\n')
        self.write(rpc_fn_code,'#endif  /* RPC_FNCODE */')
        rpc_fn_code.close()

    """
    ****************************************************************
    Function to create get_this_core() function in created server file

    INPUT : 1. file object of the server file opened in write mode
            2. Name of the core
    ****************************************************************
    """
    def server_get_this_core ( self , file_obj , corename ):
        ret_val = self.enumFnCore[corename]
        file_obj.write('\nenumFnCore  get_this_core ( void )\n'
                        '{\n    return ('+ret_val+');\n}\n\n')

    """
    ****************************************************************
    Function to generate hash_function() function in created server file

    INPUT : 1. file object of the server file opened in write mode
    ****************************************************************
    """
    def server_hash_function ( self , file_obj ):
        file_obj.write('\n int get_hash_index ( char* pc_fnName )\n{')
        file_obj.write('\n    int iTotal = 0;\n\n')
        file_obj.write('    while ( *pc_fnName )\n')
        file_obj.write('    {\n')
        file_obj.write('        iTotal ^= *pc_fnName++ ; \n')
        file_obj.write('    }\n\n')
        file_obj.write('    iTotal = iTotal % FNCODE_HASHPRIME ;\n')
        file_obj.write('    return ( iTotal );\n}\n')

    """
    ****************************************************************
    Function to generate fn_table_init() function in created server file

    INPUT : 1. file object of the server file opened in write mode
            2. Name of the core
    ****************************************************************
    """
    def server_fn_table_init ( self , file_obj , corename ):
        #Get the list of native functions
        if  ( corename == "rpc_core1" ) :
            list_core = self.core1_fn_list
        elif ( corename == "rpc_core2" ):
            list_core = self.core2_fn_list
        else:
            list_core = self.core3_fn_list

        #write the function definition
        file_obj.write('void    fn_table_init ( void )\n{\n')
        #write the list of native function in Init table function
        for j in range ( len ( list_core ) ) :
            fn_name , fn_code , params , Core , has_out = self.get_fn_details ( list_core[j] , corename )
            fn_str = '    INIT_FN_TABLE( "'+fn_name+'_S" , '+fn_code+ ' , ' +fn_name+ '_S , ' +Core+ ' , ' +str(has_out)+ ');\n'
            file_obj.write(fn_str)

        #Get the list of Non-native functions
        if  ( corename == "rpc_core1" ) :
            list_core_1 = self.core2_fn_list
            list_core_2 = self.core3_fn_list
        elif ( corename == "rpc_core2" ):
            list_core_1 = self.core1_fn_list
            list_core_2 =self.core3_fn_list
        else:
            list_core_1 = self.core1_fn_list
            list_core_2 = self.core2_fn_list

        list_core_final = list_core_1 + list_core_2
        for j1 in range ( len ( list_core_final ) ) :
            fn_name , fn_code , params , Core , has_out = self.get_fn_details ( list_core_final[j1] , corename )
            fn_name_str = 'NULL'
            fn_str = '    INIT_FN_TABLE( "'+fn_name+'_S" , '+ fn_code + ' , ' + fn_name_str + ' , ' + Core    + ' , ' + str(has_out) + ');\n'
            file_obj.write(fn_str)

        file_obj.write('    return;\n}\n\n')


    """
    ****************************************************************
    Function to get the details of the function from the prototype

    INPUT : Function prototype
    OUTPUT : 1. function name
             2. Function code for the specified function
             3. List of parameters
             4. Core in which the function is defined
             5. Has output or not
    ***************************************************************
    """
    def get_fn_details ( self , fn_prototype , corename ):
        has_out = 0
        Core = self.fn_native[fn_prototype]
        Core = self.enumFnCore[Core]
        fn_name = fn_prototype.split()[1]
        params = fn_prototype.split('(')[1]
        params = params[:-1]
        params = params.split(',')
        for i in range(len(params)):
            if "OUT" in params[i]:
                has_out = 1

        fn_code = self.enumRpcFnCode[fn_name]
        return fn_name , fn_code , params , Core , has_out

    """
    ****************************************************************
    Function to generate the _S functions for the native functions

    INPUT : 1. Server_file object
            2. function prototype from the list
            3. Corename
    ****************************************************************
    """
    def server_get_params_struct ( self , fn_name , params , file_obj ) :
        IsPointer = False
        IsString = False
        IsArraySize = False
        IsArray = False

        call_str = 'eRpcErr = '+  fn_name +'('
        
        for fn in range(len(params)):
                           
            if 'RPC_TYPE_ARRAY' in params[fn]:
                IsArray = True
            if '*' in params[fn]:
                IsPointer = True
            elif 'RPC_STRING' in params[fn]:
                IsString = True
            elif 'RPCARR_SIZE' in params[fn]:
                IsArraySize = True

            param_str = params[fn].split()
            if (len(param_str) == 2):#if IN tag is not specified
                param_str.append('')
                param_str[2] = param_str[1]
                param_str[1] = param_str[0]
                param_str[0] = 'IN'
                
                
            len_str = param_str[2]
            if ')' in len_str :
                len_str = len_str[:-1]
      
                
            arg_type = self.enumArgDir[param_str[0]]
            file_obj.write('        sArg['+str(fn)+'].e_arg_type = '+arg_type+';\n')
            file_obj.write('        sArg['+str(fn)+'].arg_len = ')

            if IsString :
                strlen_str = 'strlen((const char*)p_data) + 1 ;'
            elif IsArraySize :
            	strlen_str = 'sizeof('+param_str[2]+') ;'
            elif IsArray:
                if ( fn < len(params)):
            	    strlen_str = '(*sArg['+str(fn-1)+'].p_arg) * ( sizeof ('+param_str[2]+'));'
            	    #call_str = call_str + '('+param_str[2]+')(sArg['+str(fn)+'].p_arg),\n        '
            else:
                strlen_str = 'sizeof('+param_str[1]+') ;'

            file_obj.write(strlen_str+'\n')
            file_obj.write('        sArg['+str(fn)+'].p_arg = (unsigned int*)p_data;\n')
            file_obj.write('        p_data += sArg['+str(fn)+'].arg_len;\n\n')

            if IsArray:
                call_str = call_str + '('+param_str[2]+'*)(sArg['+str(fn)+'].p_arg),\n        '
            elif IsArraySize:
                call_str = call_str + '*( ('+param_str[2]+'*)(sArg['+str(fn)+'].p_arg) ),\n        '
            elif IsPointer :
                call_str = call_str + '('+param_str[1]+'*)(sArg['+str(fn)+'].p_arg),\n        '
            elif IsString:
                call_str = call_str + '('+param_str[1]+')(sArg['+str(fn)+'].p_arg),\n        '

            else:
                call_str = call_str + '*( ('+param_str[1]+'*)(sArg['+str(fn)+'].p_arg) ),\n        '

            IsString = False
            IsArray = False
            IsPointer = False
            IsArraySize = False

        call_str = call_str[:-10]
        call_str = call_str+');\n\n'
        file_obj.write('        '+call_str)

    """
    ****************************************************************
    Function to generate the _S functions for the native functions

    INPUT : 1. Server_file object
            2. function prototype from the list
            3. Corename
    ****************************************************************
    """
    def server_create_SFunction ( self , file_obj , fn_prototype , corename ):
        #static  enumRpcErr  fn_compute_mod_S (void *p_param)
        fn_name , fn_code , params , Core , has_out = self.get_fn_details ( fn_prototype , corename )
        fn_str = fn_prototype.split()
        if (len(params) == 1):
            if 'void' in params[0]:
                file_obj.write(fn_str[0]+" "+fn_str[1]+"_S ( void *p_param )\n{\n    enumRpcErr  eRpcErr;\n    (void)p_param;\n\n")
                call_str = '    eRpcErr = '+  fn_name +'( );\n    return (eRpcErr);\n}\n'
                file_obj.write(call_str)
                return
            
        file_obj.write(fn_str[0]+" "+fn_str[1]+"_S ( void *p_param)\n")
        file_obj.write("{\n")
        file_obj.write("    structArg       sArg["+str(len(params))+"];\n")
        file_obj.write("    unsigned char  *p_data  = (unsigned char *)p_param;\n")
        file_obj.write("    enumRpcErr      eRpcErr = RPCERR_SUCCESS;\n\n")
        file_obj.write("    if ( NULL != p_param )\n    {\n")
        self.server_get_params_struct ( fn_name , params , file_obj )
        file_obj.write('    }\n    return (eRpcErr);\n\n}\n')

    """
    ****************************************************************
    Function to generate the server file for the specifed core

    INPUT : Name of the core
    ****************************************************************
    """
    def create_server_file ( self , corename ) :
        fname = os.path.join(self.src_dir, corename+"_server.c")
        server_file = open(fname,'w+')
        self.fHeader_Comment(server_file)
        """
        Determine the list of functions under the particular core
        """

        if  ( corename == "rpc_core1" ) :
            list_core = self.core1_fn_list
        elif ( corename == "rpc_core2" ):
            list_core = self.core2_fn_list
        else:
            list_core = self.core3_fn_list

        self.write(server_file,'#include "rpc_fncode.h"\n')
        self.write(server_file,'#include "rpc_marshall.h"\n\n')
        #self.write(server_file,"#define HASHPRIME "+str(self.HASHPRIME )+"\n")
        self.write ( server_file ,'\n')
        #create the server functions for each of the native functions
        for k in range ( len ( list_core ) ) :
            self.server_create_SFunction ( server_file , list_core[k] , corename )
        #Create the get_this_core() function from the corename
        self.server_get_this_core ( server_file , corename )
        #Create fn_table_init() function in the server file from the corename
        self.server_fn_table_init ( server_file , corename )
        #Create the hash function in the server file
        self.server_hash_function ( server_file )
        server_file.close()

        """
    ****************************************************************
    Function to generate param struct

    INPUT : 1. Function prototype
            2. Name of the core
            3. Client file object

    ****************************************************************
    """

    def client_get_params_struct ( self , fn_name , params , file_obj ) :
        IsPointer = False
        IsArray = False
        IsArraySize = False
        IsString = False

        for fn in range(len(params)):

            if 'RPC_TYPE_ARRAY' in params[fn] :
                IsArray = True
            elif '*' in params[fn]:
            	IsPointer = True
            elif 'RPC_STRING' in params[fn]:
                IsString = True
            elif 'RPCARR_SIZE' in params[fn]:
            	IsArraySize = True

            param_str = params[fn].split()
            if (len(param_str) == 2):#if IN tag is not specified
                param_str.append('')
                param_str[2] = param_str[1]
                param_str[1] = param_str[0]
                param_str[0] = 'IN'
            len_str = param_str[2]
            if ')' in len_str :
                len_str = len_str[:-1]

            param_str[2] = len_str

            arg_type = self.enumArgDir[param_str[0]]
            file_obj.write('    sArg['+str(fn)+'].e_arg_type = '+arg_type+';\n')
            file_obj.write('    sArg['+str(fn)+'].arg_len = ')

            if IsString :
                strlen_str = 'strlen((const char*)'+param_str[2]+') + 1;'

            elif IsArraySize:
            	strlen_str = 'sizeof('+param_str[2]+');'

            elif IsArray:
            	if (fn < len(params) ):
            	    strlen_str = '(*sArg['+str(fn-1)+'].p_arg) *( sizeof('+param_str[2]+') );'

            else:
                strlen_str = 'sizeof('+param_str[1]+') ;'

            file_obj.write(strlen_str+'\n')


            if (IsArray or IsArraySize):
            	str_p = param_str[3]

            	if '*' in param_str[3]:
            	    str_p = re.sub('\*','',param_str[3])
            	if ')' in str_p:
            	    str_p = str_p[:-1]
            	if IsArray:
                    file_obj.write('    sArg['+str(fn)+'].p_arg = (unsigned int*)'+str_p+';\n')
            	else:
            	    file_obj.write('    sArg['+str(fn)+'].p_arg = (unsigned int*)&'+str_p+';\n')

            elif (IsPointer or IsString):
            	str_p = param_str[2]
            	if '*' in param_str[2]:
                    str_p = re.sub('\*', '', param_str[2])                                                       
                    file_obj.write('    sArg['+str(fn)+'].p_arg = (unsigned int*)'+ str_p+';\n')
            	else:
                    file_obj.write('    sArg['+str(fn)+'].p_arg = (unsigned int*)'+ str_p+';\n')
            	
                
            else:
            	str_p = param_str[2]
            	file_obj.write('    sArg['+str(fn)+'].p_arg = (unsigned int*)&'+ str_p +';\n')

            file_obj.write('\n')

            IsString = False
            IsArraySize = False
            IsArray = False

        call_str = "eRpcErr = rpc_marshal ( "
        call_str = call_str + '"'+fn_name+'_S" , '+self.enumRpcFnCode[fn_name] + ', ' + str(len(params)) + ', '
        call_str = call_str + '&(sArg[0]) )'
        call_str = call_str+';\n\n'
        file_obj.write('    '+call_str)
        file_obj.write('    return (eRpcErr);\n}\n')


    """
    ****************************************************************
    Function to generate _C functions for the function prototype

    INPUT : 1. Fucntion prototype
            2. Name of the core
            3. Client file object
    ****************************************************************
    """
    def client_C_fn_create(self,fn_proto, corename , file_obj):
        fn_name , fn_code , params , Core , has_out = self.get_fn_details ( fn_proto , corename )
        fn_str = fn_proto.split()
        str_c = "\nstatic "
        for f in range(len(fn_str)):
            if ( f == 1 ):
                fn_str[1] = fn_str[1]+"_C"
            if 'void' in fn_str[f]:
                file_obj.write(str_c+'(void)\n{\n    enumRpcErr  eRpcErr;\n')
                call_str = "eRpcErr = rpc_marshal ( "
                call_str = call_str + '"'+fn_name+'_S" , '+self.enumRpcFnCode[fn_name] + ', ' + str(0) + ', '
                call_str = call_str + 'NULL )'
                call_str = call_str+';\n\n'
                file_obj.write('    '+call_str)
                file_obj.write('    return (eRpcErr);\n}\n')
                return
                
            str_c = str_c + fn_str[f] +' '
        str_c  = str_c[:-2]
        file_obj.write(str_c+'\n{\n')
        file_obj.write('    structArg   sArg['+str(len(params))+'];\n    enumRpcErr  eRpcErr;\n\n')
        self.client_get_params_struct ( fn_name , params , file_obj )


    """
    ****************************************************************
    Function to generate the server file for the specifed core

    INPUT : Name of the core
    ****************************************************************
    """
    def create_client_file ( self , corename ) :
        IsPointer = False
        IsArraySize = False
        IsArray = False

        fname = os.path.join(self.src_dir, corename+"_client.c")
        client_file = open(fname,'w+')
        self.fHeader_Comment(client_file)

        self.write(client_file, '#include "rpc_fncode.h"\n#include "rpc_marshall.h"\n\n')

        #Get the list of Non-native functions
        if  ( corename == "rpc_core1" ) :
            list_core_1 = self.core2_fn_list
            list_core_2 = self.core3_fn_list
        elif ( corename == "rpc_core2" ):
            list_core_1 = self.core1_fn_list
            list_core_2 =self.core3_fn_list
        else:
            list_core_1 = self.core1_fn_list
            list_core_2 = self.core2_fn_list

        list_core_final = list_core_1 + list_core_2

        #write the functions calling _C functions
        for fn in range(len(list_core_final)):
            self.client_C_fn_create(list_core_final[fn], corename , client_file)
            proto = list_core_final[fn]
            proto = proto[:-1]
            self.write(client_file , proto+'\n{\n')

            fn_name , fn_code , params , Core , has_out = self.get_fn_details ( list_core_final[fn] , corename )
            self.write( client_file , '    return ( '+fn_name+'_C (')
            if len(params) == 1:
                if 'void' in params[0]:
                    self.write( client_file , '));\n}\n')
                    continue   # skip only THIS (void) function; keep generating the rest
                    
            param_str = ''
            for par in range(len(params)):
                if 'RPCARR_SIZE' in params[par]:
            	    IsArraySize = True
                elif 'RPC_TYPE_ARRAY' in params[par]:
            	    IsArray = True
                elif '*' in params[par]:
                    IsPointer = True

                line = params[par].split()
                
                if (len(line) == 2):#if IN tag is not specified
                    line.append('')
                    line[2] = line[1]
                    line[1] = line[0]
                    line[0] = 'IN'
                
                
                line_str = line[2]

                if IsArraySize or IsArray:
                    line_str = line[3]
                    if '*' in line_str:
                        line_str = re.sub('\*','',line_str)
                if IsPointer and '*' in line_str:
                    line_str = re.sub('\*', '', line_str)

                param_str = param_str + line_str + ' , '
                IsArraySize = False
                IsArray = False
                IsPointer = False

            param_str = param_str[:-2]
            self.write( client_file, param_str + ' );\n}\n')


        client_file.close()
    
   
    def create_aidl_file(self):
        fname = os.path.join(self.script_dir, "newaidl.aidl")
        aidl_file = open(fname,'w+')
        #write the package info in aidl file
        self.write(aidl_file, '\n\ninterface IElekService\n {\n')

        #Get the list of all functions
        list_core_final = self.core1_fn_list + self.core2_fn_list + self.core3_fn_list

        #write the functions calling _C functions
        for fn in range(len(list_core_final)):
            fn_name = list_core_final[fn].split()[1]
            params = list_core_final[fn].split('(')[1]
            params = params[:-1]
            params = params.split(',')
            
            self.write(aidl_file , '    IBinder '+fn_name+'_aidl (')
            par_str=''
            for p in range(len(params)):
                par_str = par_str+params[p]+','
            par_str = par_str[:-1]            
            self.write(aidl_file,par_str+');\n')
            
        self.write(aidl_file,'}')                    
        aidl_file.close()





