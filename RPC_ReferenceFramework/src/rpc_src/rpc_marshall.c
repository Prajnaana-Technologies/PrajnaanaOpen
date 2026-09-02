/*
 * Copyright (c) 2026 Prajnaana Technologies
 * SPDX-License-Identifier: MIT
 *
 * Part of the Multi-Core RPC Framework.
 * See the LICENSE file in the project root for the full license text.
 *
 * Original Author: Mamatha BV
 */

/* ****************************************************************************
 * THIS IS AUTO-GENERATED CODE
 * SHALL NOT BE MODIFIED MANUALLY; ALL CHANGES WILL BE LOST WHEN THE FILE IS
 * AUTO-GENERATED AGAIN
 * ****************************************************************************/
#include "rpc_fncode.h"
#include "rpc_marshall.h"
#include "osal.h"

#define RPC_PRIV_SIZE   sizeof(structMarshall)

structFnInfo    rpc_fn_table [FNCODE_HASHPRIME];
void    rpc_recv (void *pv_arg);

static  void    rpc_pack_arguments (unsigned char *pDst, unsigned int arg_count, structArg *ps_arg)
{
    if ( (NULL != pDst) && (NULL != ps_arg) )
    {
        while (0 < arg_count--)
        {
            if (0 != ps_arg->arg_len)
            {
                if ( (RPCARG_OUT == ps_arg->e_arg_type) || (NULL == ps_arg->p_arg) )
                {
                    os_memclr (pDst, ps_arg->arg_len);    /* Clear the space with ZEROs */
                }
                else
                {
                    os_memcpy (pDst, ps_arg->p_arg, ps_arg->arg_len);
                }
                pDst += ps_arg->arg_len;
            }
            ps_arg++;
        }
    }
    return;
}

static  void    rpc_unpack_arguments (unsigned char *pSrc, unsigned int arg_count, structArg *ps_arg)
{
    if ( (NULL != pSrc) && (NULL != ps_arg) )
    {
        while (0 < arg_count--)
        {
            if ( (NULL != ps_arg->p_arg) && (0 != ps_arg->arg_len) &&
                 (RPCARG_IN != ps_arg->e_arg_type))
            {
                os_memcpy (ps_arg->p_arg, pSrc, ps_arg->arg_len);
            }
            pSrc += ps_arg->arg_len;
            ps_arg++;
        }
    }
    return;
}

static  unsigned int    rpc_compute_checksum (unsigned int *pv_data, unsigned int data_size)
{
    unsigned int   *pui_data  = (unsigned int *)pv_data;;
    unsigned int    ui_chksum = 0u;

    data_size /= sizeof(unsigned int);
    while (0 < data_size--)
    {
        ui_chksum += (*pui_data++);       /* Add the words as is */
        //ui_chksum ^= RPCHDR_START_SIGN;
    }
    ui_chksum = (0 - ui_chksum);
    return (ui_chksum);
}

static  enumRpcErr  rpc_return_response (enumRpcErr      eRpcErr,
                                         structMarshall *ps_marshall,
                                         unsigned int    rpc_bufsize)
{
    ps_marshall->rpc_return_err   = eRpcErr;
    ps_marshall->eReqResponse     = REQ_RESPONSE_RETURNED;
    ps_marshall->rpc_hdr_checksum = 0;  /* Very important */
    ps_marshall->rpc_hdr_checksum = rpc_compute_checksum ((void *)ps_marshall, RPC_PRIV_SIZE);
    printf("Sending Return Response from %d core to %d core for function %s\n",
    		ps_marshall ->e_src_core,ps_marshall->e_dst_core , ps_marshall->fn_name);

    eRpcErr = os_send_rpc_buffer (ps_marshall->e_src_core, rpc_bufsize, (void *)ps_marshall);
    if (RPCERR_SUCCESS != eRpcErr)
    {
        os_free_rpc_buffer (ps_marshall->e_src_core, (void *)ps_marshall, rpc_bufsize);
    }
    return (eRpcErr);
}

static  unsigned int    rpc_get_fn_code (char *fn_name)
{
    unsigned int    i = get_hash_index (fn_name);
    unsigned int    j;

    for (j = 0; j < FNCODE_HASHPRIME; j++)
    {
        if (0 == strncmp (rpc_fn_table[i].fn_name, fn_name, sizeof(rpc_fn_table[i].fn_name)))
        {
            break;
        }
        i++;
        if (FNCODE_HASHPRIME <= i)
        {
            i = 0;
        }
    }
    if (FNCODE_HASHPRIME == j)
    {
        i = j;
    }
    return i;
}

enumRpcErr  rpc_marshal (char *fn_name, enumRpcFnCode fn_code, unsigned int arg_count, structArg *ps_arg)
{
    structMarshall *ps_marshall;
    structFnInfo   *ps_fn_info;
    enumRpcErr      eRpcErr;
    unsigned int    rpc_bufsize;
    int             i;

    /* First compute the total size of shared memory required. */
    ps_fn_info  = &(rpc_fn_table [fn_code]);
    rpc_bufsize = RPC_PRIV_SIZE;
    printf("rpc_marshal(): RPC Buff size = %d\n",rpc_bufsize);
    for (i = 0; i < arg_count; i++)
    {
        rpc_bufsize += ps_arg[i].arg_len;
    }
    printf("rpc_marshall: Fn=%s rpc_bufsize = %d\n", fn_name, rpc_bufsize);

    /* Allocate memory for the required size */
    ps_marshall = (structMarshall *) os_alloc_rpc_buffer (ps_fn_info->e_fn_core, rpc_bufsize);
    if (NULL == ps_marshall)
    {
        eRpcErr = RPCERR_MALLOC_FAILED;
        printf ("    rpc_marshall: os_alloc_rpc_buffer() for size %d Failed **\n", rpc_bufsize);
        goto exit_rpc_marshall;
    }

    /* Fill the rpc-marshll private structure */
    os_memclr (ps_marshall, RPC_PRIV_SIZE);
    strncpy (ps_marshall->fn_name, fn_name, MAX_FN_NAME_SIZE);
    ps_marshall->rpc_start_sign = RPCHDR_START_SIGN;
    ps_marshall->rpc_end_sign   = RPCHDR_END_SIGN;
    ps_marshall->rpc_version    = RPC_VERSION;
    ps_marshall->e_src_core     = get_this_core ();
    ps_marshall->e_dst_core     = ps_fn_info->e_fn_core;
    ps_marshall->pkt_size       = rpc_bufsize;

    /* Now fill-in the arguments for the RPC */
    rpc_pack_arguments ((unsigned char *)(ps_marshall + 1), arg_count, ps_arg);

    if (ps_fn_info->p_wait_sema)
    {
        /* There are 1 or more output results expected from the RPC being invoked. */
        ps_marshall->eReqResponse = REQ_RESPONSE_AWAITED;

        os_lock (ps_fn_info->p_fn_mutex);
        ps_marshall->fn_rpc_instance  = ps_fn_info->fn_rpc_instance;
        ps_marshall->rpc_hdr_checksum = rpc_compute_checksum ((void *)ps_marshall, RPC_PRIV_SIZE);

        /* Call the function to send out the RPC buffer */
        printf("rpc_marshall(): Dest core to send buffer = %d\n",ps_marshall->e_dst_core);
        eRpcErr = os_send_rpc_buffer (ps_marshall->e_dst_core, rpc_bufsize, (void *)ps_marshall);
        ps_marshall = NULL;
        if (RPCERR_SUCCESS == eRpcErr)
        {
            /* There are 1 or more output results expected from the RPC being invoked.
             * So pend on the semaphore.
             */
            printf ("    rpc_marshall: ... waiting for return response\n");
            i = os_sem_pend (ps_fn_info->p_wait_sema, FN_RESP_WAIT_TIMEOUT);
            ps_fn_info->fn_rpc_instance++;
            if (OS_SEM_TIMEOUT != i)
            {
                printf ("    rpc_marshall: ... response received\n");
                ps_marshall = (structMarshall *)(ps_fn_info->p_resp_buf);
                ps_fn_info->p_resp_buf = NULL;
            }
            else
            {
                printf ("    rpc_marshall: ... response TIMEOUT **\n");
                eRpcErr = RPCERR_RESPONSE_TIMEOUT;
            }
        }
        os_unlock (ps_fn_info->p_fn_mutex);
        if (NULL != ps_marshall)
        {
            /* Response received. Unpack the arguments and write the output values
             * into ps_arg before returning.
             */
            eRpcErr = ps_marshall->rpc_return_err;
            if ( (RPCERR_SUCCESS == eRpcErr) || (RPCERR_VERSION_MISMATCH == eRpcErr) )
            {
                rpc_unpack_arguments ((unsigned char *)(ps_marshall + 1), arg_count, ps_arg);
            }

            /* Now release the shared memory block */
            os_free_rpc_buffer (ps_fn_info->e_fn_core, (void *)ps_marshall, rpc_bufsize);
        }
    }
    else
    {
        /* No OUTPUT expected from the RPC. Just send the prepared RPC buffer
         * and exit.
         */
        //os_lock (ps_fn_info->p_fn_mutex);
        ps_marshall->fn_rpc_instance = ps_fn_info->fn_rpc_instance++;
        //os_unlock (ps_fn_info->p_fn_mutex);

        ps_marshall->rpc_hdr_checksum = rpc_compute_checksum ((void *)ps_marshall, RPC_PRIV_SIZE);

        eRpcErr = os_send_rpc_buffer (ps_marshall->e_dst_core, rpc_bufsize, (void *)ps_marshall);
        /* Fire-and-forget send is synchronous; the buffer is done - free it
         * (the original code leaked it on every no-output RPC). */
        os_free_rpc_buffer (ps_fn_info->e_fn_core, (void *)ps_marshall, rpc_bufsize);
    }

exit_rpc_marshall:
    return (eRpcErr);
}

/* ****************************************************************************
 * MEMBERSHIP control messages. The server tells every client which cores are
 * currently in the network, using a reserved function name so rpc_demarshal can
 * tell it apart from a real RPC. Payload = RPCCORE_COUNT bytes (1 = present).
 * ****************************************************************************/
#define MEMBERS_FN_NAME   "__members__"

static void rpc_send_membership_to (enumFnCore client)
{
    unsigned int    bufsize = RPC_PRIV_SIZE + RPCCORE_COUNT;
    structMarshall *m       = (structMarshall *) os_alloc_rpc_buffer (client, bufsize);
    unsigned char  *payload;
    enumFnCore      me = get_this_core ();
    enumRpcErr      e;
    int             i;

    if (NULL == m) return;

    os_memclr (m, RPC_PRIV_SIZE);
    strncpy (m->fn_name, MEMBERS_FN_NAME, MAX_FN_NAME_SIZE);
    m->rpc_start_sign = RPCHDR_START_SIGN;
    m->rpc_end_sign   = RPCHDR_END_SIGN;
    m->rpc_version    = RPC_VERSION;
    m->e_src_core     = me;
    m->e_dst_core     = client;
    m->pkt_size       = bufsize;
    m->eReqResponse   = REQ_RESPONSE_NONE;

    payload = (unsigned char *)(m + 1);
    for (i = 0; i < RPCCORE_COUNT; i++)
    {
        payload[i] = (i == me || g_core_present[i]) ? 1 : 0;  /* include self (the server) */
    }

    m->rpc_hdr_checksum = rpc_compute_checksum ((void *)m, RPC_PRIV_SIZE);
    e = os_send_rpc_buffer (client, bufsize, (void *)m);
    (void) e;
    /* Fire-and-forget: os_send_rpc_buffer writes to the socket synchronously, so
     * the buffer is finished with whether the send succeeded or not - free it
     * either way (freeing only on failure would leak on every broadcast). */
    os_free_rpc_buffer (client, (void *)m, bufsize);
}

/* SERVER only: send the current membership bitmap to every connected client. */
void    rpc_broadcast_membership (void)
{
    enumFnCore me = get_this_core ();
    enumFnCore c;
    for (c = RPCCORE_CORE1; c < RPCCORE_COUNT; c++)
    {
        if (c != me && g_core_present[c])
        {
            rpc_send_membership_to (c);
        }
    }
}

unsigned int    rpc_demarshal (void *p_rpcbuff, unsigned int rpc_bufsize)
{
    structMarshall *ps_marshall;
    structFnInfo   *ps_fn_info;
    unsigned int    free_rpcbuf = 0;
    enumFnCore      e_my_core   = get_this_core();
    enumFnCore      e_dst_core;
    enumRpcErr      eRpcErr;
    enumRpcFnCode   e_fn_code;

    /* Get the Marshall private structure at the top of the shared memory
     * and check its validity
     */
    printf ("rpc_demarshal: rpc_bufsize = %d\n", rpc_bufsize);
    ps_marshall = (structMarshall*) p_rpcbuff;
    if ( (RPCHDR_START_SIGN != ps_marshall->rpc_start_sign) ||
         (RPCHDR_END_SIGN   != ps_marshall->rpc_end_sign)   ||
         (rpc_bufsize < ps_marshall->pkt_size)              ||
         (0 != rpc_compute_checksum((void *)ps_marshall, RPC_PRIV_SIZE)) )
    {
        /* Invalid header !! Drop the packet */
        printf ("    rpc_demarshal: INVALID RPC-Header **\n");
        goto dealloc_rpcbuf;
    }

    /* MEMBERSHIP control message from the server - not a real RPC. Adopt the
     * bitmap and drop the packet (clients receive these; the server never
     * sends them to itself). */
    if (0 == strncmp (ps_marshall->fn_name, MEMBERS_FN_NAME, MAX_FN_NAME_SIZE))
    {
        rpc_apply_membership ((unsigned char *)(ps_marshall + 1));
        goto dealloc_rpcbuf;
    }

    /* RESPONSE RPC needs to be routed to the original source */
    if (REQ_RESPONSE_RETURNED == ps_marshall->eReqResponse)
    {
        /* Need to relay this packet to the original source */
        e_dst_core = ps_marshall->e_src_core;
    }
    else
    {
        e_dst_core = ps_marshall->e_dst_core;
    }

    if (e_dst_core != e_my_core)
    {
        /* The packet needs to be relayed to a different core.
         * So, allocate the destination core specific memory,
         * copy the contents from source buffer and then send out.
         */
        void    *p_dst_mem = os_alloc_rpc_buffer (e_dst_core, rpc_bufsize);
        printf ("    rpc_demarshal: forwarding rpc_buffer to core %d\n", e_dst_core);
        if (NULL != p_dst_mem)
        {
            os_memcpy (p_dst_mem, p_rpcbuff, rpc_bufsize);
            eRpcErr = os_send_rpc_buffer (e_dst_core, rpc_bufsize, p_dst_mem);
            if (RPCERR_SUCCESS != eRpcErr)
            {
                /* There was an error in relaying the packet.
                 * Deallocate the new RPC buffer.
                 */
                printf ("    rpc_demarshal: os_send_rpc_buffer() Failed to relay buffer **\n");
                os_free_rpc_buffer (e_dst_core, p_dst_mem, rpc_bufsize);
                if (REQ_RESPONSE_AWAITED == ps_marshall->eReqResponse)
                {
                    /* Indicate error to the sender. */
                    rpc_return_response (eRpcErr, ps_marshall, rpc_bufsize);
                    goto exit_demarshall;
                }
            }
        }
        else
        {
            printf ("    rpc_demarshal: os_alloc_rpc_buffer() Failed for size = %d\n", rpc_bufsize);
        }
        goto dealloc_rpcbuf;
    }

    e_fn_code  = (enumRpcFnCode) rpc_get_fn_code (ps_marshall->fn_name);
    ps_fn_info = &(rpc_fn_table[e_fn_code]);
    printf ("    rpc_demarshal: Fn = %s e_fn_code = %d\n",  ps_marshall->fn_name, e_fn_code);
    if (FNCODE_HASHPRIME <= e_fn_code)
    {
        printf ("    rpc_demarshal: Unsupported Function Code %d\n", e_fn_code);
        if (REQ_RESPONSE_AWAITED == ps_marshall->eReqResponse)
        {
            /* Report UNSUPPORTED-FUNCTION error to the sender. */
            rpc_return_response (RPCERR_UNSUPPORTED_FUNCTION, ps_marshall, rpc_bufsize);
            goto exit_demarshall;
        }
        else
        {
            /* Fall through to deallocate RPC buffer. */
        }
    }

    /* Check if it is a RESPONSE RPC */
    else if (REQ_RESPONSE_RETURNED == ps_marshall->eReqResponse)
    {
        /* Marshalling function is waiting on the semaphore specified in the Marshall
         * private structure. Unblock it by Posting the semaphore.
         */
        if (ps_marshall->fn_rpc_instance == ps_fn_info->fn_rpc_instance)
        {
            printf ("    rpc_demarshal: Waking up the waiting Client\n");
            ps_fn_info->p_resp_buf = p_rpcbuff;
            os_sem_post (ps_fn_info->p_wait_sema);
            goto exit_demarshall;
        }
        else
        {
            /* Caller may have timed out. Fall through to deallocate RPC buffer. */
            printf ("    rpc_demarshal: Waiting Client has timed out **\n");
        }
    }
    else    /* REQ_RESPONSE_AWAITED or REQ_RESPONSE_NONE */
    {
        /* It is an RPC being invoked by the remote core. Call the server function
         * specific to the function code.
         */
        if (NULL != ps_fn_info->pfn_server)
        {
            eRpcErr = ps_fn_info->pfn_server ((unsigned char *)(ps_marshall + 1));
            if (REQ_RESPONSE_AWAITED == ps_marshall->eReqResponse)
            {
                /* Write the return code into Marshall structure */
                if (RPCERR_SUCCESS == eRpcErr)
                {
                    if (ps_marshall->rpc_version != RPC_VERSION)
                    {
                        eRpcErr = RPCERR_VERSION_MISMATCH;
                    }
                }

                /* Output needs to be returned to the caller.
                 * So, send the buffer
                 */
                rpc_return_response (eRpcErr, ps_marshall, rpc_bufsize);
                goto exit_demarshall;
            }
            else
            {
                /* No output to be sent back. Fall through to deallocate RPC buffer. */
            }
        }
    }

dealloc_rpcbuf:
    free_rpcbuf = 1;

exit_demarshall:
    /* When code directly jumps here, the RPC Buffer will not be deallocated. */
    return (free_rpcbuf);
}

/* ************************************************************************** *
 * First function to be invoked by the RPC library within the core during
 * boot up.
 * This function is auto-generated based on the function prototypes
 * ************************************************************************** */

void    rpc_init (void)
{
    /* Initialize CORE specific functon table.
     * The called function is in the auto-generated code.
     */
    os_memclr (rpc_fn_table, sizeof (rpc_fn_table));
    fn_table_init ();
    /* Create RPC-Receive threads. The server/hub core opens one receive thread
     * per client (it accepts a connection from each); every other core opens a
     * single receive thread toward the server. Which core is the server is
     * configurable (get_server_core), so ANY core can play the hub role -
     * not just CORE2. The hub relays client<->client traffic (rpc_demarshal). */
    {
        enumFnCore me     = get_this_core();
        enumFnCore server = get_server_core();
        if (rpc_this_core_is_server())
        {
            enumFnCore c;
            printf("rpc_init(): core %d is the SERVER/hub - creating RECV threads for all clients\n", me);
            /* Single-port hub: bind+listen the ONE shared port once, up front,
             * before spawning the accept threads that share it. */
            if (0 != os_server_listen_init())
            {
                printf("rpc_init(): SERVER core %d could not open the shared listen port - no clients can join\n", me);
            }
            else
            {
                for (c = RPCCORE_CORE1; c < RPCCORE_COUNT; c++)
                {
                    if (c != me)
                    {
                        /* One accept thread per potential client. The core passed
                         * is only a hint for thread count; each thread learns the
                         * ACTUAL core it services from the connection handshake. */
                        os_create_thread (rpc_recv, c);
                    }
                }
            }
        }
        else
        {
            printf("rpc_init(): core %d is a CLIENT - creating RECV thread toward server core %d\n", me, server);
            os_create_thread (rpc_recv, server);
        }
    }
    return;
}

/* ************************************************************************** *
 * Threda function invoked by the RPC-MAIN to receive RPC packets from a
 * specific core.
 * In CORE1 and CORE3 RPC-Libraries, there would be 1 instance of this thread
 * receiving from CORE2, on CORE1 it will reception will be over SharedMemory/Mailox
 * and for CORE3 reception will be over UART.
 * In CORE2 RPC-Library, there will be 2 instances of this thread - one to receive
 * data over Sharedmem/Mailbox from CORE1 and the other to receive data over UART
 * from CORE3.
 * ************************************************************************** */
void    rpc_recv (void *pv_arg)
{

    void           *p_rpcbuff;
    unsigned int    rpc_bufsize;
    //p_rpcbuff = malloc(SOCKET_READSIZE);

    enumFnCore      eRecvCore = (enumFnCore) pv_arg;
    /* Initialise the socket for this link. If it fails, end the thread cleanly
     * instead of exit()-ing the whole process (critical for Android-as-server).
     * On the SERVER, open_comm() accepts a client on the shared port and updates
     * eRecvCore to the core that actually connected (learned via the handshake);
     * the passed-in value is only a hint. On a CLIENT it stays the server core. */
    if (0 != open_comm(&eRecvCore))
    {
        if (get_this_core() != get_server_core())
        {
            /* A client that cannot reach its server: signal main()/JNI. */
            g_rpc_link_failed = 1;
            printf ("rpc_recv(): CLIENT core %d could NOT reach server core %d - giving up\n",
                    get_this_core(), eRecvCore);
        }
        else
        {
            /* One client's listen/accept failed; other clients can still join. */
            printf ("rpc_recv(): SERVER core %d could not set up link for client core %d - continuing\n",
                    get_this_core(), eRecvCore);
        }
        return;
    }
    printf ("rpc_recv(): Thread Created for receiving from core = %d\n", eRecvCore);
    while (1)
    {
        int nread = os_recv_rpc_buffer (eRecvCore, &p_rpcbuff);   /* Blocking call */
        if (nread <= 0)
        {
            /* Peer closed (0) or socket error (<0): this link is gone. Free the
             * buffer os_recv_rpc_buffer allocated, update membership, and end
             * this thread (avoids the old busy-spin + leak on a dead socket). */
            if (NULL != p_rpcbuff) os_free_rpc_buffer (eRecvCore, p_rpcbuff, SOCKET_READSIZE);
            printf ("rpc_recv(): core %d: link to core %d closed (n=%d)\n",
                    get_this_core(), eRecvCore, nread);
            rpc_handle_disconnect (eRecvCore);
            break;
        }
        rpc_bufsize = (unsigned int) nread;
        if ( (NULL != p_rpcbuff) && (0u < rpc_bufsize) )
        {
            printf ("rpc_recv(): core = %d  p_rpcbuff = %p  rpc_bufsize = %d\n",
                    eRecvCore, p_rpcbuff, rpc_bufsize);

            if ( rpc_demarshal (p_rpcbuff, rpc_bufsize) )
            {
                /* Deallocate the RPC buffer */
                os_free_rpc_buffer (eRecvCore, p_rpcbuff, rpc_bufsize);
            }
        }
    }
}


/* ************************************************************************** *
 * ***************************** END OF FILE ******************************** */

