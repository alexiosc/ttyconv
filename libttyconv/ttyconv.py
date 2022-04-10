#!/usr/bin/python

import sys
import os
import signal
import pty
import select
import tty
import resource
import errno
import time
import termios
import struct
import fcntl
import optparse
import textwrap
import libttyconv.encodings
import codecs


__package_version__ = '@Version: 1.0 @'.split()[-2:][0] or '0'


__version__ = __package_version__ + '.' + ("$Rev: 25 $".split()[-2:][0] or '0')


class ExceptionPexpect (Exception):
    """
    Base class for all exceptions raised by this module.
    """
    pass



class TTYConv (object):
    """
    Convert TTY encodings.

    This is an autonomous program, not a module.
    """
    # Sets delay in close() method to allow kernel time to update process
    # status. Time in seconds.
    CLOSE_DELAY = 0.1

    # Sets delay in terminate() method to allow kernel time to update process
    # status. Time in seconds.
    TERMINATE_DELAY = 0.1

    # File numbers.
    STDIN_FILENO = pty.STDIN_FILENO
    STDOUT_FILENO = pty.STDOUT_FILENO
    STDERR_FILENO = pty.STDERR_FILENO

    # Version
    VERSION = '$Id: ttyconv.py 25 2009-03-16 18:37:57Z alexios $'

    # Usage
    USAGE = '-r REMOTE-ENCODING [ -l LOCAL-ENCOFING ] [ OPTIONS ... ] [ COMMAND ... ]'


    def __init__(self):
        self.progname = sys.argv[0] or 'ttyconv'

        self.initialised = False
        self.stdin = sys.stdin
        self.stdout = sys.stdout
        self.stderr = sys.stderr

        self.terminated = True
        self.status = None # status returned by os.waitpid
        self.flag_eof = False
        self.pid = None
        self.child_fd = -1 # initially closed
        self.closed = False

        self.options, self.cmdline = self.parseCommandLineArguments()
        self.validateCommandLineArguments()

        self.log("Transcoding remote %s terminal to %s." % (self.options.remote, self.options.local))

        if not self.cmdline:
            self.cmdline = [os.environ.get("SHELL", "/bin/bash")]
        
        self.initSignals()
        self.spawn (self.cmdline)
        self.initialised = True
        self.message ('Entering transcoded terminal.')
        self.interact()
        self.message ('\nDone, leaving transcoded terminal.')


    def __del__(self):

        """This makes sure that no system resources are left open. Python only
        garbage collects Python objects. OS file descriptors are not Python
        objects, so they must be handled explicitly. If the child file
        descriptor was opened outside of this class (passed to the constructor)
        then this does not close it. """

        if not self.initialised:
            return

        if not self.closed:
            # It is possible for __del__ methods to execute during the
            # teardown of the Python VM itself. Thus self.close() may
            # trigger an exception because os.close may be None.
            # -- Fernando Perez
            try:
                self.close()
            except AttributeError:
                pass


    def log (self, message): # pylint:disable-msg=R0201
        """
        Log a message, if in verbose mode.
        """
        if self.options.verbose:
            print(message)

        
    def message (self, message): # pylint:disable-msg=R0201
        """
        Print out a message.
        """
        print(message)

        
    def fail (self, message):
        """
        Produce a failure message and exit with exit code 1.
        """
        tw = textwrap.TextWrapper(subsequent_indent='    ', width=79)
        sys.stderr.write (tw.fill ("%s: %s." % (self.progname, message.rstrip('.\n'))) + '\n')
        sys.exit(1)


    def parseCommandLineArguments (self):
        """
        Parse command line arguments.
        """
        # Parse the arguments.
        parser = optparse.OptionParser (usage='%prog ' + self.USAGE, version=self.VERSION)
    
        parser.add_option ('-r', '--remote',
                           dest='remote',
                           help='Set the remote encoding.',
                           )
        parser.add_option ('-l', '--local',
                           dest='local',
                           help='Set the local encoding (default: try to guess from locale settings).',
                           )
        parser.add_option ('-n', '--nolocale',
                           action='store_true', dest='nolocale',
                           help='Do not modify the locale for the child terminal ' + \
                               ' (default: modify any settings which specify an encoding).',
                           )
        parser.add_option ('-v', '--verbose',
                           action='store_true', dest='verbose',
                           help='Print out more information (default: only minimal information).',
                           )
        parser.add_option ('', '--list',
                           action='store_true', dest='list',
                           help='List available encodings.',
                           )

        return parser.parse_args()


    def validateCommandLineArguments (self):
        """
        Validate the command line arguments.
        """
        # Parse the arguments.
        if self.options.list:
            tw = textwrap.TextWrapper(subsequent_indent=' ' * 21, width=59)
            print("%-20s %s" % ('ENCODING', 'ALIASES (LANGUAGES)'))
            print("-" * 79)
            for enc, aliases, lang in libttyconv.encodings.encodings:
                text = "%s (%s)" % (str(aliases).replace('None', enc), lang)
                print("%-20s %s" % (enc, tw.fill(text)))
            sys.exit(0)

        # No -r specified?
        if not self.options.remote:
            self.fail ("the remote encoding must be specified")
            sys.exit(1)

        # Validate the encodings.
        self.options.remote = self.options.remote.upper()
        try:
            ''.encode(self.options.remote)
        except LookupError:
            self.fail("remote encoding '%s' is unknown. Try %s --list for a list of valid encodings." % (self.options.remote, self.progname))

        # Validate the local encoding. Guess it if necessary.
        if not self.options.local:
            self.options.local = self.guessEncoding().upper()
        else:
            self.options.local = self.options.local.upper()
            try:
                ''.encode(self.options.remote)
            except LookupError:
                self.fail("the remote encoding is unknown")
                sys.exit(1)


    def guessEncoding (self):
        """
        Try to guess the local encoding from the locale settings.
        """
        for key in ['LC_ALL', 'LC_CTYPE', 'LANG']:
            val = os.environ.get(key)

            if val:
                try:
                    locale, encoding = val.split('.') # pylint:disable-msg=W0612
                    ''.encode(encoding)
                    return encoding
                except (ValueError, LookupError):
                    continue
        self.fail ('unable to detect the local encoding. Specify it explicitly using the -l option.')


    def initSignals(self):
        """
        Initialise signal handlers.
        """
        def signal_handler (sig, sf):
            """
            Propagate received signals to the child.
            """
            if self.isalive():
                self.kill (sig)

        def sigwinch_handler (sig, data):
            """
            Pass SIGWINCH events (window size change) to the child process.
            """
            r, c = self.getwinsize (sys.stdout.fileno())
            self.setwinsize (self.child_fd, r, c)

        # Install signal handlers.
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGPIPE,
                       signal.SIGCONT):
            signal.signal (signum, signal_handler)

        # Set the SIGWINCH handler.
        signal.signal (signal.SIGWINCH, sigwinch_handler)


    def spawn(self, args):
        """
        Start the remote terminal.
        """
        # The pid and child_fd of this object get set by this method.
        # Note that it is difficult for this method to fail.
        # You cannot detect if the child process cannot start.
        # So the only way you can tell if the child process started
        # or not is to try to read from the file descriptor. If you get
        # EOF immediately then it means that the child is already dead.
        # That may not necessarily be bad because you may haved spawned a child
        # that performs some task; creates no stdout output; and then dies.

        # Get the current window size.
        r, c = self.getwinsize (self.STDIN_FILENO)

        # Solaris uses our custom __fork_pty(). All others use pty.fork().
        if (sys.platform.lower().find ('solaris') >= 0) or (sys.platform.lower().find ('sunos5') >= 0):
            use_native_pty_fork = False
        else:
            use_native_pty_fork = True

        # Fork the PTY.
        if use_native_pty_fork:
            try:
                self.pid, self.child_fd = pty.fork()
            except OSError as e:
                raise ExceptionPexpect('Error! pty.fork() failed: ' + str(e))

        # Use internal __fork_pty() instead (handle Solaris bug).
        else:
            self.pid, self.child_fd = self.__fork_pty()

        # This is the child process
        if self.pid == 0:
            try:
                self.child_fd = sys.stdout.fileno() # used by setwinsize()
                self.setwinsize (self.child_fd, r, c)

            except Exception:
                # Some platforms do not like setwinsize (Cygwin).
                # This will cause problem when running applications that
                # are very picky about window size.
                # This is a serious limitation, but not a show stopper.
                pass

            # Do not allow child to inherit open file descriptors from parent.
            max_fd = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
            for i in range (3, max_fd):
                try:
                    os.close (i)
                except OSError:
                    pass

            # I don't know why this works, but ignoring SIGHUP fixes a
            # problem when trying to start a Java daemon with sudo
            # (specifically, Tomcat).
            signal.signal(signal.SIGHUP, signal.SIG_IGN)

            self.log ('Executing: %s' % ' '.join (args))
            if self.options.nolocale:
                self.log ('Not setting locale.')
                os.execvp (args[0], args)
            else:
                os.execvpe (args[0], args, self.setLocale())

        # Parent
        self.terminated = False
        self.closed = False


    def setLocale(self):
        """
        Convert the locale settings to the new encoding.
        """
        # Modify the locale settings, if they're present.
        env = dict()
        for key, val in os.environ.items():
            if key == 'LANG' or key.startswith ('LC_'):
                if val.endswith ('.' + self.options.local.upper()):
                    val = val.split('.')[0] + '.' + self.options.remote.upper()
            env[key] = val
        return env
            

    def getwinsize(self, fd):

        """This returns the terminal window size of the child tty. The return
        value is a tuple of (rows, cols). """

        TIOCGWINSZ = getattr(termios, 'TIOCGWINSZ', 1074295912)
        s = struct.pack('HHHH', 0, 0, 0, 0)
        x = fcntl.ioctl(fd, TIOCGWINSZ, s)
        return struct.unpack('HHHH', x)[0:2]


    def setwinsize(self, fd, r, c):

        """This sets the terminal window size of the child tty. This will cause
        a SIGWINCH signal to be sent to the child. This does not change the
        physical window size. It changes the size reported to TTY-aware
        applications like vi or curses -- applications that respond to the
        SIGWINCH signal. """

        # Check for buggy platforms. Some Python versions on some platforms
        # (notably OSF1 Alpha and RedHat 7.1) truncate the value for
        # termios.TIOCSWINSZ. It is not clear why this happens.
        # These platforms don't seem to handle the signed int very well;
        # yet other platforms like OpenBSD have a large negative value for
        # TIOCSWINSZ and they don't have a truncate problem.
        # Newer versions of Linux have totally different values for TIOCSWINSZ.
        # Note that this fix is a hack.
        TIOCSWINSZ = getattr(termios, 'TIOCSWINSZ', -2146929561)
        if TIOCSWINSZ == 2148037735: # L is not required in Python >= 2.2.
            TIOCSWINSZ = -2146929561 # Same bits, but with sign.
        # Note, assume ws_xpixel and ws_ypixel are zero.
        s = struct.pack('HHHH', r, c, 0, 0)
        fcntl.ioctl(fd, TIOCSWINSZ, s)
        #print("Set window size for fd %d to %dx%d" % (fd, c, r))


    def __fork_pty(self):
        """
        This implements a substitute for the forkpty system call. This
        should be more portable than the pty.fork() function. Specifically,
        this should work on Solaris.

        Modified 10.06.05 by Geoff Marshall: Implemented __fork_pty() method to
        resolve the issue with Python's pty.fork() not supporting Solaris,
        particularly ssh. Based on patch to posixmodule.c authored by Noah
        Spurrier::

        http://mail.python.org/pipermail/python-dev/2003-May/035281.html
        """
        parent_fd, child_fd = os.openpty()
        if parent_fd < 0 or child_fd < 0:
            raise ExceptionPexpect("Error! Could not open pty with os.openpty().")

        pid = os.fork()
        if pid < 0:
            raise ExceptionPexpect("Error! Failed os.fork().")
        elif pid == 0:
            # Child.
            os.close(parent_fd)
            self.__pty_make_controlling_tty(child_fd)

            os.dup2(child_fd, 0)
            os.dup2(child_fd, 1)
            os.dup2(child_fd, 2)

            if child_fd > 2:
                os.close(child_fd)
        else:
            # Parent.
            os.close(child_fd)

        return pid, parent_fd


    def __pty_make_controlling_tty(self, tty_fd):

        """This makes the pseudo-terminal the controlling tty. This should be
        more portable than the pty.fork() function. Specifically, this should
        work on Solaris. """

        child_name = os.ttyname(tty_fd)

        # Disconnect from controlling tty if still connected.
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
        if fd >= 0:
            os.close(fd)

        os.setsid()

        # Verify we are disconnected from controlling tty
        try:
            fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
            if fd >= 0:
                os.close(fd)
                raise ExceptionPexpect("Error! We are not disconnected from a controlling tty.")
        except:
            # Good! We are disconnected from a controlling tty.
            pass

        # Verify we can open child pty.
        fd = os.open(child_name, os.O_RDWR)
        if fd < 0:
            raise ExceptionPexpect("Error! Could not open child pty, " + child_name)
        else:
            os.close(fd)

        # Verify we now have a controlling tty.
        fd = os.open("/dev/tty", os.O_WRONLY)
        if fd < 0:
            raise ExceptionPexpect("Error! Could not open controlling tty, /dev/tty")
        else:
            os.close(fd)

    def isalive(self):

        """This tests if the child process is running or not. This is
        non-blocking. If the child was terminated then this will read the
        exitstatus or signalstatus of the child. This returns True if the child
        process appears to be running or False if not. It can take literally
        SECONDS for Solaris to return the right status. """

        if self.terminated:
            return False

        if self.flag_eof:
            # This is for Linux, which requires the blocking form of waitpid to get
            # status of a defunct process. This is super-lame. The flag_eof would have
            # been set in read_nonblocking(), so this should be safe.
            waitpid_options = 0
        else:
            waitpid_options = os.WNOHANG

        try:
            pid, status = os.waitpid(self.pid, waitpid_options)
        except OSError as e: # No child processes
            if e[0] == errno.ECHILD:
                raise ExceptionPexpect ('isalive() encountered condition where "terminated" is 0, but there was no child process. Did someone else call waitpid() on our process?')
            else:
                raise e

        # I have to do this twice for Solaris. I can't even believe that I figured this out...
        # If waitpid() returns 0 it means that no child process wishes to
        # report, and the value of status is undefined.
        if pid == 0:
            try:
                pid, status = os.waitpid(self.pid, waitpid_options) ### os.WNOHANG) # Solaris!
            except OSError as e: # This should never happen...
                if e[0] == errno.ECHILD:
                    raise ExceptionPexpect ('isalive() encountered condition that should never happen. There was no child process. Did someone else call waitpid() on our process?')
                else:
                    raise e

            # If pid is still 0 after two calls to waitpid() then the process
            # really is alive. This seems to work on all platforms, except for
            # Irix which seems to require a blocking call on waitpid or select,
            # so I let read_nonblocking take care of this situation
            # (unfortunately, this requires waiting through the timeout).
            if pid == 0:
                return True

        if pid == 0:
            return True

        if os.WIFEXITED (status):
            self.status = status
            self.terminated = True

        elif os.WIFSIGNALED (status):
            self.status = status
            self.terminated = True

        elif os.WIFSTOPPED (status):
            self.fail ('the remote terminal process is stopped. This is not allowed. ' + \
                           'Is some other process attempting job control with our child PID?')
        return False


    def kill (self, sig):
        """
        Send the given signal to the child application.

        In keeping with UNIX tradition, it has a misleading name. It does not
        necessarily kill the child unless you send the right signal.
        """
        # Same as os.kill, but the pid is given for you.
        if self.isalive():
            os.kill (self.pid, sig)


    def terminate(self, force=False):
        """
        Force the child process to terminate.  
        
        It starts nicely with SIGHUP and SIGINT. If "force" is True then moves
        onto SIGKILL. This returns True if the child was terminated. This
        returns False if the child could not be terminated.
        """
        if not self.isalive():
            return True

        try:
            self.kill (signal.SIGHUP)
            time.sleep (self.TERMINATE_DELAY)
            if not self.isalive():
                return True

            self.kill (signal.SIGCONT)
            time.sleep (self.TERMINATE_DELAY)
            if not self.isalive():
                return True

            self.kill (signal.SIGINT)
            time.sleep (self.TERMINATE_DELAY)

            if not self.isalive():
                return True

            if force:
                self.kill (signal.SIGKILL)
                time.sleep (self.TERMINATE_DELAY)
                if not self.isalive():
                    return True
                else:
                    return False
            return False

        except OSError as e:
            # I think there are kernel timing issues that sometimes cause
            # this to happen. I think isalive() reports True, but the
            # process is dead to the kernel.
            # Make one last attempt to see if the kernel is up to date.
            time.sleep (self.TERMINATE_DELAY)

            # Return False if the child is still alive.
            return not self.isalive()


    def close (self, force=True):
        """
        Close the connection to the child terminal.

        Note that calling close() more than once is valid. This emulates
        standard Python behavior with files. Set ``force`` to True to make sure
        that the child is terminated (SIGKILL is sent if the child ignores
        SIGHUP and SIGINT).
        """
        if not self.closed:
            os.close (self.child_fd)
            time.sleep(self.CLOSE_DELAY) # Give kernel time to update process status.
            if self.isalive():
                if not self.terminate(force):
                    raise ExceptionPexpect ('close() could not terminate the child using terminate()')
            self.child_fd = -1
            self.closed = True


    def write (self, fd, data):
        """
        Write to the specified file descriptor.
        """
        while data != '' and self.isalive():
            n = os.write(fd, data)
            data = data[n:]


    def read (self, fd):
        """
        Read from the specified file descriptor.
        """
        return os.read (fd, 1024)


    def select (self, iwtd, owtd, ewtd): # pylint:disable-msg=R0201
        """
        Wrap select.select(), ignoring signals.

        If select.select raises a select.error exception and errno is an EINTR
        error then it is ignored. Mainly this is used to ignore SIGWINCH
        (terminal resize), which we handle separately.
        """
        # if select() is interrupted by a signal (errno == EINTR) then
        # we loop back and enter the select() again.
        while True:
            try:
                return select.select (iwtd, owtd, ewtd)

            except select.error as e:
                if e[0] != errno.EINTR:
                    raise


    def x_lenientCodec (self, codec, string, replace='?'):
        """
        Decode string into a unicode object, replacing bad characters with '?'
        (or ``replace``, if specified).

        This method is meant to be used as a back-end for lenientEncode() and
        lenientDecode().

        ``codec`` must be a closure to perform the appropriate encoding or
        decoding operation.
    
        ``encoding`` sets the encoding s is assumed to be in.
        """
        # Much, MUCH easier to ask for forgiveness than permission.
        retval = ''
        s_left = string
    
        while s_left:
            try:
                retval += codec(s_left)
                #print("RETVAL:%s\r" % type(retval))
                break
    
            except UnicodeError as e:
                retval += codec(s_left[:e.start]) + replace
                #print("EXC:%s\r" % type(replace))
                s_left = s_left[e.end:]
    
        return retval


    def lenientDecode (self, string, encoding, replace='?'):
        """
        Translate an encoded string to a unicode object.

        Translation errors are ignored, with offending characters replaced by
        '?' (or the value of ``replace``).
        """

        return codecs.decode(string, encoding=encoding, errors='replace')
                                    
            
    def lenientEncode (self, string, encoding, replace='?'):
        """
        Translate a unicode object to an encoded string.

        Translation errors are ignored, with offending characters replaced by
        '?' (or the value of ``replace``).
        """
        return codecs.encode(string, encoding=encoding, errors='replace')


    def remoteToLocal (self, s):
        """
        Transcode the string ``s`` from the remote encoding to the local one.
        """
        s = self.lenientDecode (s, self.options.remote)
        s = self.lenientEncode (s, self.options.local)
        return s


    def localToRemote (self, s):
        """
        Transcode the string ``s`` from the local encoding to the remote one.
        """
        s = self.lenientDecode (s, self.options.local)
        s = self.lenientEncode (s, self.options.remote)
        return s


    def interact (self):
        """
        Connect the local and remote terminals, transcoding data.

        The remote terminal's output is transcoded from the remote encoding to
        the local encoding and sent to the local terminal's output.

        The local terminal's input is transcoded from the local encoding to the
        remote encoding and sent to the remote terminal's input.
        """
        mode = tty.tcgetattr (self.STDIN_FILENO)
        tty.setraw (self.STDIN_FILENO)

        try:
            try:
                while self.isalive():
                    r, w, e = self.select ([self.child_fd, self.STDIN_FILENO], [], [])
        
                    # Data: remote to local.
                    if self.child_fd in r:
                        data = self.read (self.child_fd)
                        data = self.remoteToLocal (data)
                        os.write (self.STDOUT_FILENO, data)
        
                    # Data: local to remote.
                    if self.STDIN_FILENO in r:
                        data = self.read (self.STDIN_FILENO)
                        data = self.localToRemote (data)
                        self.write (self.child_fd, data)

            except OSError as e:
                # This seems to be raised on logout from bash (and possibly
                # others). Ignore it.
                if e.args[0] != errno.EIO:
                    self.fail(e.args[1])

        finally:
            tty.tcsetattr (self.STDIN_FILENO, tty.TCSAFLUSH, mode)

    

def run():
    """
    Entry point to the program.
    """
    TTYConv()


# Run the program.
if __name__ == '__main__':
    run()


# End of file.
