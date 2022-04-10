#!/bin/env python3

import asyncio
import fcntl
import os
import signal
import struct
import sys
import termios
import tty
import codecs
import argparse
import functools
import logging


def encoding(s):
    """Parse an encoding name."""
    try:
        enc = codecs.lookup(s)
        if not hasattr(enc, "incrementaldecoder"):
            raise argparse.ArgumentTypeError("unsuitable encoder.")
    except:
        raise argparse.ArgumentTypeError("encoding not found.")

    return enc



def create_task(coroutine, loop=None, **kwargs):
    """
    This helper function wraps a ``loop.create_task(coroutine())`` call and ensures there is
    an exception handler added to the resulting task. If the task raises an exception it is logged
    using the provided ``logger``, with additional context provided by ``message`` and optionally
    ``message_args``.
    """
    if loop is None:
        obj = asyncio
    else:
        obj = loop
    task = obj.create_task(coroutine, **kwargs)
    task.add_done_callback(
        functools.partial(_handle_task_result)
    )
    return task


def _handle_task_result(task):
    try:
        task.result()
    except asyncio.CancelledError:
        pass  # Task cancellation should not be logged as an error.
    # Ad the pylint ignore: we want to handle all exceptions here so that the result of the task
    # is properly logged. There is no point re-raising the exception in this callback.
    except Exception:  # pylint: disable=broad-except
        print("Caught unhandled exception")
        sys.exit(1)
    

class TTYConv2:
    """
    This program replaces a lot of the older functionality of a channel handler:

    * bbsgetty
    * bbslogin
    * emud
    * the bash session loop
    """

    def __init__(self):
        self.parse_command_line()

        self.old_termios = termios.tcgetattr(0)
        self._exitcode = None
        self._mainloop = None
        self.done = False
            


    def parse_command_line(self):
        parser = argparse.ArgumentParser(description="Transcode terminal sessions")

        parser.add_argument(metavar="REMOTE-ENCODING", dest="remote_encoding",
                            type=encoding,
                            help="""Specify the remote encoding. Run
                            the program with --list for a list of
                            available encodings.""")
                            
        parser.add_argument("-l", "--local-encoding", metavar="LOCAL-ENCODING",
                            default=encoding("utf-8"),
                            help="""Specify the local encoding. (default: utf-8)""")
                            
        parser.add_argument("COMMAND", nargs=argparse.REMAINDER,
                            help="""The command to transcode. Add two dashes (--) before the command if
                            it includes command line options.""")

        self.args = parser.parse_args()

        # Early sanity checks
        if not os.isatty(sys.stdin.fileno()) or \
           not os.isatty(sys.stdout.fileno()):
            parser.error("Standard input and output must both be TTYs.")

        return self.args


    def handle_exception(self, loop, context):
        msg = context.get("exception", context["message"])
        # if 'exception' in context:
        #     import pprint
        #     pprint.pprint(context['exception'])
        #     import traceback
        #     print(traceback.format_exception(context['exception'], None, True))

        print(f"Exception was never caught: {msg}")
        #create_task(self.shutdown(failure_msg=msg))


    def fail(self, exitcode=1):
        self._exitcode = exitcode
        if self._mainloop is not None:
            self._mainloop.create_task(self.shutdown())
        else:
            sys.exit(self._exitcode)


    async def _shutdown(self):
        self._mainloop.stop()


    async def shutdown(self, signal=None, failure_msg: str=None):
        """Cleanup tasks tied to the service's shutdown."""

        if failure_msg:
            print("Shutting down.", failure_msg)

        self.done = True

        # Try to update the channel status. Ignore all exceptions at this point.
        # if self.bbsd is not None and self.channel is not None:
        #     try:
        #         coroutine = self.bbsd.set_channel_state(
        #             megistos.channels.FAILED,
        #             desc=failure_msg,
        #             errors=True)
        #         task = asyncio.create_task(coroutine, timeout=1)
        #         await asyncio.gather(task)
        #     except:
        #         pass

        if signal:
            print(f"Received exit signal {signal.name}...")

        tasks = [t for t in asyncio.all_tasks() if t is not
                 asyncio.current_task()]

        for task in tasks:
            task.cancel()

        #await asyncio.gather(*tasks)
        #print(f"Flushing metrics")
        asyncio.ensure_future(self._shutdown())


    def terminal_resized(self):
        """Handle the WINCH signal, issued when the terminal emulator window
        has changed size.  Pass this onto the session.
        """
        if self.pty_fd is None:
            return

        try:
            cols, rows = os.get_terminal_size()
            if cols > 0 and rows > 0:
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                #print(f"\r\n\033[0;7mResizing terminal to {cols}×{rows}\033[0m\r\n")
                fcntl.ioctl(self.pty_fd, termios.TIOCSWINSZ, winsize)
        except Exception as e:
            print(f"Failed to updated terminal window size: {e}")


    def init_mainloop(self):
        loop = asyncio.get_event_loop()
        signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
        for s in signals:
            loop.add_signal_handler(
                s, lambda s=s: create_task(self.shutdown(signal=s)))
        loop.set_exception_handler(self.handle_exception)
        return loop


    def run(self):
        self.remote_decoder = self.args.remote_encoding.incrementaldecoder(errors="replace")
        self.remote_encoder = self.args.remote_encoding.incrementalencoder(errors="replace")
        self.local_encoding = self.args.local_encoding
        
        try:
            # Initialise the main loop
            self._mainloop = loop = self.init_mainloop()

            loop.add_signal_handler(signal.SIGWINCH, self.terminal_resized)

            loop.add_reader(sys.stdin.fileno(), self.handle_fd_read, sys.stdin.fileno())

            #create_task(self.ticks(), loop=loop)
            #print("---5")
            create_task(self.session(), loop=loop)

            loop.run_forever()

        except SystemExit as e:
            if self._mainloop is not None:
                loop.close()
            raise

        finally:
            if self._mainloop is not None:
                loop.close()
            if self._exitcode == 0:
                print("Done.")
            sys.exit(self._exitcode)


    # async def ticks(self):
    #     while True:
    #         await asyncio.sleep(5)
    #         print("Tick!")


    def handle_fd_read(self, fd):
        if self.done:
            return

        #print(f"INPUT AVAILABLE: fd={fd}")
        try:
            data = os.read(fd, 8192)

        except OSError as e:
            if e.args[0] == 5:
                # Restore original termios settings
                termios.tcsetattr(0, termios.TCSAFLUSH, self.old_termios)

                print("Session ended (server side).")
                create_task(self.shutdown(failure_msg="End of session"))
                return

        #print(f"INPUT AVAILABLE: fd={fd}, data=\"{data}\"")
        chars, num_bytes = self.local_encoding.decode(data, "replace")
        if num_bytes:
            # Use an incremental encoder for the remote.
            data_out = self.remote_encoder.encode(chars, "replace")
            if data_out:
                os.write(self.pty_fd, data_out)


    def handle_output_from_system(self, fd):
        """This reads output from the system (the server side of the session)
        and transmits it to the user (the client side) and any
        emulating (output-watching) sessions.
        """
        
        if self.done:
            return

        try:
            data = os.read(fd, 8192)

        except OSError as e:
            if e.args[0] == 5:

                # Restore original termios settings
                termios.tcsetattr(0, termios.TCSAFLUSH, self.old_termios)

                print("Command ended.")
                create_task(self.shutdown(failure_msg="End of session"))
                return

        chars = self.remote_decoder.decode(data)
        if chars:
            # This is an incremental decoder, so wait till we have at least
            # one fully decoded character.
            data_out, num_bytes = self.local_encoding.encode(chars, "replace")
            os.write(1, data_out)


    # def handle_fd_write(self, fd):
    #     os.write ( self.output.get()
    #     pass


    async def session(self):
        # Initialize the terminal size
        try:
            cols, rows = os.get_terminal_size()
            assert cols > 0 and rows > 0
        except Exception as e:
            cols, rows = 80, 25 # Sane defaults if this didn't work.

        pid, fd = os.forkpty()

        # Try to get the slave PTS name. Python doesn't yet have an
        # os.ptsname() function, so we have to call the libc one directly.
        try:
            import ctypes
            libc6 = ctypes.CDLL('libc.so.6')
            ptsname = libc6.ptsname
            ptsname.restype = ctypes.c_char_p
            #print(f"ptsname({fd}) = {ptsname(fd)}")

        except Exception as e:
            print("Failed to get pts name: %s", e)

        self.pty_fd = fd

        #print(f"({pid},{fd})")
        command = self.args.COMMAND
        try:
            if pid == 0:
                #print(f"PID={os.getpid()}: exec(2) {command} ... Terminal size is {cols}x{rows}")
                try:
                    winsize = struct.pack("HHHH", rows, cols, 0, 0)
                    fcntl.ioctl(0, termios.TIOCSWINSZ, winsize)
                except Exception as e:
                    print(f"Failed to set pseudoterminal size to {cols}x{rows}: {e}")
    
                sys.stderr.flush()
    
                # Set up the environment
                env = os.environ
                #env["PATH"] = "."                  # TODO: Make this a config value
                
                os.execvpe(command[0], command, env)
                #os.execvpe("/bin/bash", ["/bin/bash"], env)
                # The child process never gets to this line.
    
            else:
                tty.setraw(0, when=termios.TCSANOW)
    
            #print(f"Child PID is {pid}, PTY FD is {fd}")
    
            self._mainloop.add_reader(fd, self.handle_output_from_system, fd)
            #self._mainloop.add_writer(fd, self.handle_fd_write, fd)

        except Exception as e:
            print("Whoops: {}".format(e))
            raise


if __name__ == "__main__":
    TTYConv2().run()
    