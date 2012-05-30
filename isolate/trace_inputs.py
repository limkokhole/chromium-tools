#!/usr/bin/env python
# coding=utf-8
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Traces an executable and its child processes and extract the files accessed
by them.

The implementation uses OS-specific API. The native Kernel logger and the ETL
interface is used on Windows. Dtrace is used on OSX. Strace is used otherwise.
The OS-specific implementation is hidden in an 'API' interface.

The list is done in two phases, the first is to do the actual trace and generate
an technique-specific log file. Then the log file is parsed to extract the
information, including the individual child processes and the files accessed
from the log.
"""

import codecs
import csv
import glob
import json
import logging
import optparse
import os
import posixpath
import re
import subprocess
import sys
import weakref

## OS-specific imports

if sys.platform == 'win32':
  from ctypes.wintypes import create_unicode_buffer
  from ctypes.wintypes import windll, FormatError  # pylint: disable=E0611
  from ctypes.wintypes import GetLastError  # pylint: disable=E0611
elif sys.platform == 'darwin':
  import Carbon.File  #  pylint: disable=F0401


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(BASE_DIR))

KEY_TRACKED = 'isolate_dependency_tracked'
KEY_UNTRACKED = 'isolate_dependency_untracked'


## OS-specific functions

if sys.platform == 'win32':
  def QueryDosDevice(drive_letter):
    """Returns the Windows 'native' path for a DOS drive letter."""
    assert re.match(r'^[a-zA-Z]:$', drive_letter), drive_letter
    # Guesswork. QueryDosDeviceW never returns the required number of bytes.
    chars = 1024
    drive_letter = unicode(drive_letter)
    p = create_unicode_buffer(chars)
    if 0 == windll.kernel32.QueryDosDeviceW(drive_letter, p, chars):
      err = GetLastError()
      if err:
        # pylint: disable=E0602
        raise WindowsError(
            err,
            'QueryDosDevice(%s): %s (%d)' % (
              str(drive_letter), FormatError(err), err))
    return p.value


  def GetShortPathName(long_path):
    """Returns the Windows short path equivalent for a 'long' path."""
    long_path = unicode(long_path)
    # Adds '\\\\?\\' when given an absolute path so the MAX_PATH (260) limit is
    # not enforced.
    if os.path.isabs(long_path) and not long_path.startswith('\\\\?\\'):
      long_path = '\\\\?\\' + long_path
    chars = windll.kernel32.GetShortPathNameW(long_path, None, 0)
    if chars:
      p = create_unicode_buffer(chars)
      if windll.kernel32.GetShortPathNameW(long_path, p, chars):
        return p.value

    err = GetLastError()
    if err:
      # pylint: disable=E0602
      raise WindowsError(
          err,
          'GetShortPathName(%s): %s (%d)' % (
            str(long_path), FormatError(err), err))


  def GetLongPathName(short_path):
    """Returns the Windows long path equivalent for a 'short' path."""
    short_path = unicode(short_path)
    # Adds '\\\\?\\' when given an absolute path so the MAX_PATH (260) limit is
    # not enforced.
    if os.path.isabs(short_path) and not short_path.startswith('\\\\?\\'):
      short_path = '\\\\?\\' + short_path
    chars = windll.kernel32.GetLongPathNameW(short_path, None, 0)
    if chars:
      p = create_unicode_buffer(chars)
      if windll.kernel32.GetLongPathNameW(short_path, p, chars):
        return p.value

    err = GetLastError()
    if err:
      # pylint: disable=E0602
      raise WindowsError(
          err,
          'GetLongPathName(%s): %s (%d)' % (
            str(short_path), FormatError(err), err))


  def get_current_encoding():
    """Returns the 'ANSI' code page associated to the process."""
    return 'cp%d' % int(windll.kernel32.GetACP())


  class DosDriveMap(object):
    """Maps \Device\HarddiskVolumeN to N: on Windows."""
    # Keep one global cache.
    _MAPPING = {}

    def __init__(self):
      if not self._MAPPING:
        # This is related to UNC resolver on windows. Ignore that.
        self._MAPPING['\\Device\\Mup'] = None

        for letter in (chr(l) for l in xrange(ord('C'), ord('Z')+1)):
          try:
            letter = '%s:' % letter
            mapped = QueryDosDevice(letter)
            # It can happen. Assert until we see it happens in the wild. In
            # practice, prefer the lower drive letter.
            assert mapped not in self._MAPPING
            if mapped not in self._MAPPING:
              self._MAPPING[mapped] = letter
          except WindowsError:  # pylint: disable=E0602
            pass

    def to_dos(self, path):
      """Converts a native NT path to DOS path."""
      match = re.match(r'(^\\Device\\[a-zA-Z0-9]+)(\\.*)?$', path)
      assert match, path
      if not match.group(1) in self._MAPPING:
        # Unmapped partitions may be accessed by windows for the
        # fun of it while the test is running. Discard these.
        return None
      drive = self._MAPPING[match.group(1)]
      if not drive or not match.group(2):
        return drive
      return drive + match.group(2)


  def isabs(path):
    """Accepts X: as an absolute path, unlike python's os.path.isabs()."""
    return os.path.isabs(path) or len(path) == 2 and path[1] == ':'


  def get_native_path_case(path):
    """Returns the native path case for an existing file.

    On Windows, removes any leading '\\?\'.
    """
    assert isabs(path), path
    # Windows used to have an option to turn on case sensitivity on non Win32
    # subsystem but that's out of scope here and isn't supported anymore.
    # Go figure why GetShortPathName() is needed.
    path = GetLongPathName(GetShortPathName(path))
    if path.startswith('\\\\?\\'):
      return path[4:]
    return path


elif sys.platform == 'darwin':


  # On non-windows, keep the stdlib behavior.
  isabs = os.path.isabs


  def get_native_path_case(path):
    """Returns the native path case for an existing file."""
    assert isabs(path), path
    # Technically, it's only HFS+ on OSX that is case insensitive. It's the
    # default setting on HFS+ but can be changed.
    rel_ref, _ = Carbon.File.FSPathMakeRef(path)
    return rel_ref.FSRefMakePath()


else:  # OSes other than Windows and OSX.


  # On non-windows, keep the stdlib behavior.
  isabs = os.path.isabs


  def get_native_path_case(path):
    """Returns the native path case for an existing file.

    On OSes other than OSX and Windows, assume the file system is
    case-sensitive.

    TODO(maruel): This is not strictly true. Implement if necessary.
    """
    assert isabs(path), path
    # Give up on cygwin, as GetLongPathName() can't be called.
    return path


def get_flavor():
  """Returns the system default flavor. Copied from gyp/pylib/gyp/common.py."""
  flavors = {
    'cygwin': 'win',
    'win32': 'win',
    'darwin': 'mac',
    'sunos5': 'solaris',
    'freebsd7': 'freebsd',
    'freebsd8': 'freebsd',
  }
  return flavors.get(sys.platform, 'linux')


def isEnabledFor(level):
  return logging.getLogger().isEnabledFor(level)


def fix_python_path(cmd):
  """Returns the fixed command line to call the right python executable."""
  out = cmd[:]
  if out[0] == 'python':
    out[0] = sys.executable
  elif out[0].endswith('.py'):
    out.insert(0, sys.executable)
  return out


def posix_relpath(path, root):
  """posix.relpath() that keeps trailing slash."""
  out = posixpath.relpath(path, root)
  if path.endswith('/'):
    out += '/'
  return out


def cleanup_path(x):
  """Cleans up a relative path. Converts any os.path.sep to '/' on Windows."""
  if x:
    x = x.rstrip(os.path.sep).replace(os.path.sep, '/')
  if x == '.':
    x = ''
  if x:
    x += '/'
  return x


class ApiBase(object):
  """OS-agnostic API to trace a process and its children."""
  class Context(object):
    """Processes one log line at a time and keeps the list of traced processes.
    """
    class Process(object):
      """Keeps context for one traced child process.

      Logs all the files this process touched. Ignores directories.
      """
      def __init__(self, root, pid, initial_cwd, parentid):
        """root is a reference to the Context."""
        assert isinstance(root, ApiBase.Context)
        assert isinstance(pid, int), repr(pid)
        self.root = weakref.ref(root)
        self.pid = pid
        # Children are pids.
        self.children = []
        self.parentid = parentid
        self.initial_cwd = initial_cwd
        self.cwd = None
        self.files = set()

      def add_file(self, filepath):
        if self.root().blacklist(unicode(filepath)):
          return
        logging.debug('add_file(%d, %s)' % (self.pid, filepath))
        self.files.add(filepath)

    def __init__(self, blacklist):
      self.blacklist = blacklist
      self.processes = {}

    def resolve(self):
      """Resolve all the filenames and returns them."""
      files = set()
      non_existent = set()
      for p in self.processes.itervalues():
        for filepath in p.files:
          filepath = unicode(filepath)
          # For late-bound file paths, it could be blacklisted after all the
          # processes are processed so it needs to be checked again.
          if self.blacklist(filepath):
            break
          if os.path.isfile(filepath):
            files.add(filepath)
          else:
            non_existent.add(filepath)
      return files, non_existent

  @staticmethod
  def clean_trace(logname):
    """Deletes the old log."""
    raise NotImplementedError()

  @classmethod
  def gen_trace(cls, cmd, cwd, logname, output):
    """Runs the OS-specific trace program on an executable.

    Since the logs are per pid, we need to log the list of the initial pid.
    """
    raise NotImplementedError(cls.__class__.__name__)

  @classmethod
  def parse_log(cls, filename, blacklist):
    """Processes a trace log and returns the files opened and the files that do
    not exist.

    It does not track directories.

    Most of the time, files that do not exist are temporary test files that
    should be put in /tmp instead. See http://crbug.com/116251.

    Returns a tuple (existing files, non existing files, nb_processes_created)
    """
    raise NotImplementedError(cls.__class__.__name__)


class Strace(ApiBase):
  """strace implies linux."""
  IGNORED = (
    '/bin',
    '/dev',
    '/etc',
    '/lib',
    '/proc',
    '/sys',
    '/tmp',
    '/usr',
    '/var',
  )

  class Context(ApiBase.Context):
    """Processes a strace log line and keeps the list of existent and non
    existent files accessed.

    Ignores directories.

    Uses late-binding to processes the cwd of each process. The problem is that
    strace generates one log file per process it traced but doesn't give any
    information about which process was started when and by who. So we don't
    even know which process is the initial one. So process the logs out of
    order and use late binding with RelativePath to be able to deduce the
    initial directory of each process once all the logs are parsed.
    """
    class Process(ApiBase.Context.Process):
      """Represents the state of a process.

      Contains all the information retrieved from the pid-specific log.
      """
      # Function names are using ([a-z_0-9]+)
      # This is the most common format. function(args) = result
      RE_HEADER = re.compile(r'^([a-z_0-9]+)\((.+?)\)\s+= (.+)$')
      # An interrupted function call, only grab the minimal header.
      RE_UNFINISHED = re.compile(r'^([^\(]+)(.*) \<unfinished \.\.\.\>$')
      # A resumed function call.
      RE_RESUMED = re.compile(r'^<\.\.\. ([^ ]+) resumed> (.+)$')
      # A process received a signal.
      RE_SIGNAL = re.compile(r'^--- SIG[A-Z]+ .+ ---')
      # A process didn't handle a signal.
      RE_KILLED = re.compile(r'^\+\+\+ killed by ([A-Z]+) \+\+\+$')
      # A call was canceled.
      RE_UNAVAILABLE = re.compile(r'\)\s+= \? <unavailable>$')
      # Happens when strace fails to even get the function name.
      UNNAMED_FUNCTION = '????'

      # Arguments parsing.
      RE_CHDIR = re.compile(r'^\"(.+?)\"$')
      RE_EXECVE = re.compile(r'^\"(.+?)\", \[.+?\], \[.+?\]$')
      RE_OPEN2 = re.compile(r'^\"(.*?)\", ([A-Z\_\|]+)$')
      RE_OPEN3 = re.compile(r'^\"(.*?)\", ([A-Z\_\|]+), (\d+)$')
      RE_RENAME = re.compile(r'^\"(.+?)\", \"(.+?)\"$')

      class RelativePath(object):
        """A late-bound relative path."""
        def __init__(self, parent, value):
          self.parent = parent
          self.value = value

        def render(self):
          """Returns the current directory this instance is representing.

          This function is used to return the late-bound value.
          """
          if self.value and self.value.startswith(u'/'):
            # An absolute path.
            return self.value
          parent = self.parent.render() if self.parent else u'<None>'
          if self.value:
            return os.path.normpath(os.path.join(parent, self.value))
          return parent

        def __unicode__(self):
          """Acts as a string whenever needed."""
          return unicode(self.render())

        def __str__(self):
          """Acts as a string whenever needed."""
          return str(self.render())

      def __init__(self, root, pid):
        super(Strace.Context.Process, self).__init__(root, pid, None, None)
        # The dict key is the function name of the pending call, like 'open'
        # or 'execve'.
        self._pending_calls = {}
        self._line_number = 0
        # Current directory when the process started.
        self.initial_cwd = self.RelativePath(self.root(), None)

      def get_cwd(self):
        """Returns the best known value of cwd."""
        return self.cwd or self.initial_cwd

      def render(self):
        """Returns the string value of the RelativePath() object.

        Used by RelativePath. Returns the initial directory and not the
        current one since the current directory 'cwd' validity is time-limited.

        The validity is only guaranteed once all the logs are processed.
        """
        return self.initial_cwd.render()

      def on_line(self, line):
        self._line_number += 1
        if self.RE_SIGNAL.match(line):
          # Ignore signals.
          return

        match = self.RE_KILLED.match(line)
        if match:
          self.handle_exit_group(match.group(1), None, None)
          return

        match = self.RE_UNFINISHED.match(line)
        if match:
          assert match.group(1) not in self._pending_calls
          self._pending_calls[match.group(1)] = match.group(1) + match.group(2)
          return

        match = self.RE_UNAVAILABLE.match(line)
        if match:
          # This usually means a process was killed and a pending call was
          # canceled.
          # TODO(maruel): Look up the last exit_group() trace just above and
          # make sure any self._pending_calls[anything] is properly flushed.
          return

        match = self.RE_RESUMED.match(line)
        if match:
          assert match.group(1) in self._pending_calls, self._pending_calls
          pending = self._pending_calls.pop(match.group(1))
          # Reconstruct the line.
          line = pending + match.group(2)

        match = self.RE_HEADER.match(line)
        assert match, (self.pid, self._line_number, line)
        if match.group(1) == self.UNNAMED_FUNCTION:
          return
        handler = getattr(self, 'handle_%s' % match.group(1), None)
        assert handler, (self.pid, self._line_number, line)
        try:
          return handler(
              match.group(1),
              match.group(2),
              match.group(3))
        except Exception:
          print >> sys.stderr, (self.pid, self._line_number, line)
          raise

      def handle_chdir(self, _function, args, result):
        """Updates cwd."""
        assert result.startswith('0'), 'Unexecpected fail: %s' % result
        cwd = self.RE_CHDIR.match(args).group(1)
        self.cwd = self.RelativePath(self, cwd)
        logging.debug('handle_chdir(%d, %s)' % (self.pid, self.cwd))

      def handle_clone(self, _function, _args, result):
        """Transfers cwd."""
        if result == '? ERESTARTNOINTR (To be restarted)':
          return
        # Update the other process right away.
        childpid = int(result)
        child = self.root().get_or_set_proc(childpid)
        # Copy the cwd object.
        child.initial_cwd = self.get_cwd()
        assert child.parentid is None
        child.parentid = self.pid
        self.children.append(childpid)

      def handle_close(self, _function, _args, _result):
        pass

      def handle_execve(self, _function, args, result):
        self._handle_file(self.RE_EXECVE.match(args).group(1), result)

      def handle_exit_group(self, _function, _args, _result):
        """Removes cwd."""
        self.cwd = None

      @staticmethod
      def handle_fork(_function, args, result):
        assert False, (args, result)

      def handle_open(self, _function, args, result):
        args = (self.RE_OPEN3.match(args) or self.RE_OPEN2.match(args)).groups()
        if 'O_DIRECTORY' in args[1]:
          return
        self._handle_file(args[0], result)

      def handle_rename(self, _function, args, result):
        args = self.RE_RENAME.match(args).groups()
        self._handle_file(args[0], result)
        self._handle_file(args[1], result)

      @staticmethod
      def handle_stat64(_function, args, result):
        assert False, (args, result)

      @staticmethod
      def handle_vfork(_function, args, result):
        assert False, (args, result)

      def _handle_file(self, filepath, result):
        if result.startswith('-1'):
          return
        filepath = self.RelativePath(self.get_cwd(), filepath)
        self.add_file(filepath)

    def __init__(self, blacklist, initial_cwd):
      super(Strace.Context, self).__init__(blacklist)
      self.initial_cwd = initial_cwd

    def render(self):
      """Returns the string value of the initial cwd of the root process.

      Used by RelativePath.
      """
      return self.initial_cwd

    def on_line(self, pid, line):
      self.get_or_set_proc(pid).on_line(line.strip())

    def get_or_set_proc(self, pid):
      """Returns the Context.Process instance for this pid or creates a new one.
      """
      assert isinstance(pid, int) and pid
      return self.processes.setdefault(pid, self.Process(self, pid))

    @classmethod
    def traces(cls):
      prefix = 'handle_'
      return [i[len(prefix):] for i in dir(cls.Process) if i.startswith(prefix)]

  @staticmethod
  def clean_trace(logname):
    if os.path.isfile(logname):
      os.remove(logname)
    # Also delete any pid specific file from previous traces.
    for i in glob.iglob(logname + '.*'):
      if i.rsplit('.', 1)[1].isdigit():
        os.remove(i)

  @classmethod
  def gen_trace(cls, cmd, cwd, logname, output):
    """Runs strace on an executable.

    Since the logs are per pid, we need to log the list of the initial pid.
    """
    logging.info('gen_trace(%s, %s, %s, %s)' % (cmd, cwd, logname, output))
    stdout = stderr = None
    if output:
      stdout = subprocess.PIPE
      stderr = subprocess.STDOUT
    traces = ','.join(cls.Context.traces())
    trace_cmd = [
      'strace',
      '-ff',
      '-s', '256',
      '-e', 'trace=%s' % traces,
      '-o', logname,
    ]
    child = subprocess.Popen(
        trace_cmd + cmd,
        cwd=cwd,
        stdin=subprocess.PIPE,
        stdout=stdout,
        stderr=stderr)
    out = child.communicate()[0]
    # Once it's done, write metadata into the log file to be able to follow the
    # pid files.
    assert not os.path.isfile(logname)
    with open(logname, 'wb') as f:
      json.dump(
          {
            'cwd': cwd,
            # The pid of strace process, not very useful.
            'pid': child.pid,
          },
          f)
    return child.returncode, out

  @classmethod
  def parse_log(cls, filename, blacklist):
    logging.info('parse_log(%s, %s)' % (filename, blacklist))
    with open(filename, 'r') as f:
      data = json.load(f)
    context = cls.Context(blacklist, data['cwd'])
    for pidfile in glob.iglob(filename + '.*'):
      pid = pidfile.rsplit('.', 1)[1]
      if pid.isdigit():
        pid = int(pid)
        # TODO(maruel): Load as utf-8
        for line in open(pidfile, 'rb'):
          context.on_line(pid, line)
    files, non_existent = context.resolve()
    # Resolve any symlink we hit.
    return (
        set(os.path.realpath(f) for f in files),
        set(os.path.realpath(f) for f in non_existent),
        len(context.processes))


class Dtrace(ApiBase):
  """Uses DTrace framework through dtrace. Requires root access.

  Implies Mac OSX.

  dtruss can't be used because it has compatibility issues with python.

  Also, the pid->cwd handling needs to be done manually since OSX has no way to
  get the absolute path of the 'cwd' dtrace variable from the probe.

  Also, OSX doesn't populate curpsinfo->pr_psargs properly, see
  https://discussions.apple.com/thread/1980539.
  """
  IGNORED = (
    '/.vol',
    '/Library',
    '/System',
    '/dev',
    '/etc',
    '/private/var',
    '/tmp',
    '/usr',
    '/var',
  )

  # pylint: disable=C0301
  # To understand the following code, you'll want to take a look at:
  # http://developers.sun.com/solaris/articles/dtrace_quickref/dtrace_quickref.html
  # https://wikis.oracle.com/display/DTrace/Variables
  # http://docs.oracle.com/cd/E19205-01/820-4221/
  #
  # The list of valid probes can be retrieved with:
  # sudo dtrace -l -P syscall | less
  D_CODE = """
      proc:::start /trackedpid[ppid]/ {
        trackedpid[pid] = 1;
        current_processes += 1;
        printf("%d %d:%d %s_%s(\\"%s\\", %d) = 0\\n",
               logindex, ppid, pid, probeprov, probename, execname,
               current_processes);
        logindex++;
      }
      proc:::exit /trackedpid[pid] && current_processes == 1/ {
        trackedpid[pid] = 0;
        current_processes -= 1;
        printf("%d %d:%d %s_%s(\\"%s\\", %d) = 0\\n",
               logindex, ppid, pid, probeprov, probename, execname,
               current_processes);
        logindex++;
        exit(0);
      }
      proc:::exit /trackedpid[pid]/ {
        trackedpid[pid] = 0;
        current_processes -= 1;
        printf("%d %d:%d %s_%s(\\"%s\\", %d) = 0\\n",
               logindex, ppid, pid, probeprov, probename, execname,
               current_processes);
        logindex++;
      }

      /* Finally what we care about! */
      syscall::open*:entry /trackedpid[pid]/ {
        self->arg0 = arg0;
        self->arg1 = arg1;
        self->arg2 = arg2;
      }
      syscall::open*:return /trackedpid[pid] && errno == 0/ {
        printf("%d %d:%d %s(\\"%s\\", %d, %d) = %d\\n",
               logindex, ppid, pid, probefunc, copyinstr(self->arg0),
               self->arg1, self->arg2, errno);
        logindex++;
        self->arg0 = 0;
        self->arg1 = 0;
        self->arg2 = 0;
      }

      syscall::rename:entry /trackedpid[pid]/ {
        self->arg0 = arg0;
        self->arg1 = arg1;
      }
      syscall::rename:return /trackedpid[pid]/ {
        printf("%d %d:%d %s(\\"%s\\", \\"%s\\") = %d\\n",
               logindex, ppid, pid, probefunc, copyinstr(self->arg0),
               copyinstr(self->arg1), errno);
        logindex++;
        self->arg0 = 0;
        self->arg1 = 0;
      }

      /* Track chdir, it's painful because it is only receiving relative path */
      syscall::chdir:entry /trackedpid[pid]/ {
        self->arg0 = arg0;
      }
      syscall::chdir:return /trackedpid[pid] && errno == 0/ {
        printf("%d %d:%d %s(\\"%s\\") = %d\\n",
               logindex, ppid, pid, probefunc, copyinstr(self->arg0), errno);
        logindex++;
        self->arg0 = 0;
      }
      /* TODO(maruel): *stat* functions and friends
        syscall::access:return,
        syscall::chdir:return,
        syscall::chflags:return,
        syscall::chown:return,
        syscall::chroot:return,
        syscall::getattrlist:return,
        syscall::getxattr:return,
        syscall::lchown:return,
        syscall::lstat64:return,
        syscall::lstat:return,
        syscall::mkdir:return,
        syscall::pathconf:return,
        syscall::readlink:return,
        syscall::removexattr:return,
        syscall::setxattr:return,
        syscall::stat64:return,
        syscall::stat:return,
        syscall::truncate:return,
        syscall::unlink:return,
        syscall::utimes:return,
      */
      """

  @classmethod
  def code(cls, pid, cwd):
    """Setups the D code to implement child process tracking.

    Injects a fake chdir() trace to simplify parsing. The reason is that the
    child process is already running at that point so:
    - no proc_start() is logged for it.
    - there is no way to figure out the absolute path of cwd in kernel on OSX

    Since the child process is already started, initialize current_processes to
    1.
    """
    pid = str(pid)
    cwd = os.path.realpath(cwd).replace('\\', '\\\\').replace('%', '%%')
    return (
        'dtrace:::BEGIN {\n'
        '  current_processes = 1;\n'
        '  logindex = 0;\n'
        '  trackedpid[') + pid + ('] = 1;\n'
        '  printf("%d %d:%d chdir(\\"' + cwd + '\\") = 0\\n",\n'
        '      logindex, 1, ' + pid + ');\n'
        '  logindex++;\n'
        '  printf("%d %d:%d %s_%s() = 0\\n",\n'
        '      logindex, ppid, pid, probeprov, probename);\n'
        '  logindex++;\n'
        '}\n') + cls.D_CODE

  class Context(ApiBase.Context):
    # This is the most common format. index pid function(args) = result
    RE_HEADER = re.compile(r'^\d+ (\d+):(\d+) ([a-zA-Z_\-]+)\((.*?)\) = (.+)$')

    # Arguments parsing.
    RE_CHDIR = re.compile(r'^\"(.+?)\"$')
    RE_OPEN = re.compile(r'^\"(.+?)\", (\d+), (-?\d+)$')
    RE_RENAME = re.compile(r'^\"(.+?)\", \"(.+?)\"$')

    O_DIRECTORY = 0x100000

    class Process(ApiBase.Context.Process):
      pass

    def on_line(self, line):
      match = self.RE_HEADER.match(line)
      assert match, line
      fn = getattr(
          self,
          'handle_%s' % match.group(3).replace('-', '_'),
          self._handle_ignored)
      return fn(
          int(match.group(1)),
          int(match.group(2)),
          match.group(3),
          match.group(4),
          match.group(5))

    def handle_dtrace_BEGIN(self, _ppid, _pid, _function, args, _result):
      pass

    def handle_proc_start(self, ppid, pid, _function, _args, result):
      """Transfers cwd."""
      assert result == '0'
      cwd = self.processes[ppid].cwd
      assert pid not in self.processes
      proc = self.processes[pid] = self.Process(self, pid, cwd, ppid)
      proc.cwd = cwd

    def handle_proc_exit(self, _ppid, pid, _function, _args, _result):
      """Removes cwd."""
      self.processes[pid].cwd = None

    def handle_chdir(self, ppid, pid, _function, args, result):
      """Updates cwd."""
      if result.startswith('0'):
        cwd = self.RE_CHDIR.match(args).group(1)
        if not cwd.startswith('/'):
          cwd2 = os.path.join(self.processes[pid].cwd, cwd)
          logging.debug('handle_chdir(%d, %s) -> %s' % (pid, cwd, cwd2))
        else:
          logging.debug('handle_chdir(%d, %s)' % (pid, cwd))
          cwd2 = cwd
        proc = self.processes.setdefault(
            pid, self.Process(self, pid, cwd2, ppid))
        proc.cwd = cwd2
      else:
        assert False, 'Unexecpected fail: %s' % result

    def handle_open_nocancel(self, ppid, pid, function, args, result):
      return self.handle_open(ppid, pid, function, args, result)

    def handle_open(self, _ppid, pid, function, args, result):
      match = self.RE_OPEN.match(args)
      assert match, (pid, function, args, result)
      args = match.groups()
      flag = int(args[1])
      if self.O_DIRECTORY & flag == self.O_DIRECTORY:
        # Ignore directories.
        return
      self._handle_file(pid, args[0], result)

    def handle_rename(self, _ppid, pid, _function, args, result):
      args = self.RE_RENAME.match(args).groups()
      self._handle_file(pid, args[0], result)
      self._handle_file(pid, args[1], result)

    def _handle_file(self, pid, filepath, result):
      if result.startswith(('-1', '2')):
        return
      if not filepath.startswith('/'):
        filepath = os.path.join(self.processes[pid].cwd, filepath)
      # We can get '..' in the path.
      filepath = os.path.normpath(filepath)
      # Sadly, still need to filter out directories here;
      # saw open_nocancel(".", 0, 0) = 0 lines.
      if os.path.isdir(filepath):
        return
      self.processes[pid].add_file(filepath)

    @staticmethod
    def _handle_ignored(_ppid, pid, function, args, result):
      logging.debug('%d %s(%s) = %s' % (pid, function, args, result))

  @staticmethod
  def clean_trace(logname):
    if os.path.isfile(logname):
      os.remove(logname)

  @classmethod
  def gen_trace(cls, cmd, cwd, logname, output):
    """Runs dtrace on an executable.

    This dtruss is broken when it starts the process itself or when tracing
    child processes, this code starts a wrapper process trace_child_process.py,
    which waits for dtrace to start, then trace_child_process.py starts the
    executable to trace.
    """
    logging.info('gen_trace(%s, %s, %s, %s)' % (cmd, cwd, logname, output))
    logging.info('Running: %s' % cmd)
    signal = 'Go!'
    logging.debug('Our pid: %d' % os.getpid())

    # Part 1: start the child process.
    stdout = stderr = None
    if output:
      stdout = subprocess.PIPE
      stderr = subprocess.STDOUT
    child_cmd = [
      sys.executable, os.path.join(BASE_DIR, 'trace_child_process.py'),
    ]
    child = subprocess.Popen(
        child_cmd + cmd,
        stdin=subprocess.PIPE,
        stdout=stdout,
        stderr=stderr,
        cwd=cwd)
    logging.debug('Started child pid: %d' % child.pid)

    # Part 2: start dtrace process.
    # Note: do not use the -p flag. It's useless if the initial process quits
    # too fast, resulting in missing traces from the grand-children. The D code
    # manages the dtrace lifetime itself.
    trace_cmd = [
      'sudo',
      'dtrace',
      '-x', 'dynvarsize=4m',
      '-x', 'evaltime=exec',
      '-n', cls.code(child.pid, cwd),
      '-o', '/dev/stderr',
      '-q',
    ]
    with open(logname, 'w') as logfile:
      dtrace = subprocess.Popen(
          trace_cmd, stdout=logfile, stderr=subprocess.STDOUT)
    logging.debug('Started dtrace pid: %d' % dtrace.pid)

    # Part 3: Read until one line is printed, which signifies dtrace is up and
    # ready.
    with open(logname, 'r') as logfile:
      while 'dtrace_BEGIN' not in logfile.readline():
        if dtrace.poll() is not None:
          break

    try:
      # Part 4: We can now tell our child to go.
      # TODO(maruel): Another pipe than stdin could be used instead. This would
      # be more consistent with the other tracing methods.
      out = child.communicate(signal)[0]

      dtrace.wait()
      if dtrace.returncode != 0:
        print 'dtrace failure: %d' % dtrace.returncode
        with open(logname) as logfile:
          print ''.join(logfile.readlines()[-100:])
        # Find a better way.
        os.remove(logname)
      else:
        # Short the log right away to simplify our life. There isn't much
        # advantage in keeping it out of order.
        cls._sort_log(logname)
    except KeyboardInterrupt:
      # Still sort when testing.
      cls._sort_log(logname)
      raise

    return dtrace.returncode or child.returncode, out

  @classmethod
  def parse_log(cls, filename, blacklist):
    logging.info('parse_log(%s, %s)' % (filename, blacklist))
    context = cls.Context(blacklist)
    for line in open(filename, 'rb'):
      context.on_line(line)
    files, non_existent = context.resolve()
    # Resolve any symlink we hit.
    return (
        set(os.path.realpath(f) for f in files),
        set(os.path.realpath(f) for f in non_existent),
        len(context.processes))

  @staticmethod
  def _sort_log(logname):
    """Sorts the log back in order when each call occured.

    dtrace doesn't save the buffer in strict order since it keeps one buffer per
    CPU.
    """
    with open(logname, 'rb') as logfile:
      lines = [f for f in logfile.readlines() if f.strip()]
    lines = sorted(lines, key=lambda l: int(l.split(' ', 1)[0]))
    with open(logname, 'wb') as logfile:
      logfile.write(''.join(lines))


class LogmanTrace(ApiBase):
  """Uses the native Windows ETW based tracing functionality to trace a child
  process.
  """
  class Context(ApiBase.Context):
    """Processes a ETW log line and keeps the list of existent and non
    existent files accessed.

    Ignores directories.
    """
    # Only the useful headers common to all entries are listed there. Any column
    # at 19 or higher is dependent on the specific event.
    EVENT_NAME = 0
    TYPE = 1
    PID = 9
    TID = 10
    PROCESSOR_ID = 11
    TIMESTAMP = 16

    class Process(ApiBase.Context.Process):
      pass

    def __init__(self, blacklist):
      super(LogmanTrace.Context, self).__init__(blacklist)
      self._drive_map = DosDriveMap()
      self._first_line = False
      # Threads mapping to the corresponding process id.
      self._threads_active = {}

    def on_csv_line(self, line):
      """Processes a CSV Event line."""
      # So much white space!
      line = [i.strip() for i in line]
      if not self._first_line:
        assert line == [
          u'Event Name',
          u'Type',
          u'Event ID',
          u'Version',
          u'Channel',
          u'Level',  # 5
          u'Opcode',
          u'Task',
          u'Keyword',
          u'PID',
          u'TID',  # 10
          u'Processor Number',
          u'Instance ID',
          u'Parent Instance ID',
          u'Activity ID',
          u'Related Activity ID',  # 15
          u'Clock-Time',
          u'Kernel(ms)',  # Both have a resolution of ~15ms which makes them
          u'User(ms)',    # pretty much useless.
          u'User Data',
        ]
        self._first_line = True
        return

      # As you can see, the CSV is full of useful non-redundant information:
      # Event ID
      assert line[2] == '0'
      # Version
      assert line[3] in ('2', '3'), line[3]
      # Channel
      assert line[4] == '0'
      # Level
      assert line[5] == '0'
      # Task
      assert line[7] == '0'
      # Keyword
      assert line[8] == '0x0000000000000000'
      # Instance ID
      assert line[12] == ''
      # Parent Instance ID
      assert line[13] == ''
      # Activity ID
      assert line[14] == '{00000000-0000-0000-0000-000000000000}'
      # Related Activity ID
      assert line[15] == ''

      if line[0].startswith('{'):
        # Skip GUIDs.
        return

      # Convert the PID in-place from hex.
      line[self.PID] = int(line[self.PID], 16)

      # By Opcode
      handler = getattr(
          self,
          'handle_%s_%s' % (line[self.EVENT_NAME], line[self.TYPE]),
          None)
      if not handler:
        # Try to get an universal fallback
        handler = getattr(self, 'handle_%s_Any' % line[self.EVENT_NAME], None)
      if handler:
        handler(line)
      else:
        assert False, '%s_%s' % (line[self.EVENT_NAME], line[self.TYPE])

    @staticmethod
    def handle_EventTrace_Header(line):
      """Verifies no event was dropped, e.g. no buffer overrun occured."""
      #BUFFER_SIZE = 19
      #VERSION = 20
      #PROVIDER_VERSION = 21
      #NUMBER_OF_PROCESSORS = 22
      #END_TIME = 23
      #TIMER_RESOLUTION = 24
      #MAX_FILE_SIZE = 25
      #LOG_FILE_MODE = 26
      #BUFFERS_WRITTEN = 27
      #START_BUFFERS = 28
      #POINTER_SIZE = 29
      EVENTS_LOST = 30
      #CPU_SPEED = 31
      #LOGGER_NAME = 32
      #LOG_FILE_NAME = 33
      #BOOT_TIME = 34
      #PERF_FREQ = 35
      #START_TIME = 36
      #RESERVED_FLAGS = 37
      #BUFFERS_LOST = 38
      #SESSION_NAME_STRING = 39
      #LOG_FILE_NAME_STRING = 40
      assert line[EVENTS_LOST] == '0'

    def handle_EventTrace_Any(self, line):
      pass

    def handle_FileIo_Create(self, line):
      """Handles a file open.

      All FileIo events are described at
      http://msdn.microsoft.com/library/windows/desktop/aa363884.aspx
      for some value of 'description'.

      " (..) process and thread id values of the IO events (..) are not valid "
      http://msdn.microsoft.com/magazine/ee358703.aspx
      """
      #IRP = 19
      TTID = 20  # Thread ID, that's what we want.
      #FILE_OBJECT = 21
      #CREATE_OPTIONS = 22
      #FILE_ATTRIBUTES = 23
      #SHARE_ACCESS = 24
      OPEN_PATH = 25

      # Find the process from the thread id.
      tid = int(line[TTID], 16)
      proc = self.processes.get(self._threads_active.get(tid))
      if not proc:
        # Not a process we care about.
        return

      match = re.match(r'^\"(.+)\"$', line[OPEN_PATH])
      raw_path = match.group(1)
      # Ignore directories and bare drive right away.
      if raw_path.endswith(os.path.sep):
        return
      filename = self._drive_map.to_dos(raw_path)
      # Ignore bare drive right away.
      if len(raw_path) == 2:
        return
      proc.add_file(filename)

    def handle_FileIo_Rename(self, line):
      # TODO(maruel): Handle?
      pass

    def handle_FileIo_Any(self, line):
      pass

    def handle_Process_Any(self, line):
      pass

    def handle_Process_DCStart(self, line):
      """Gives historic information about the process tree.

      Use it to extract the pid of the trace_inputs.py parent process that
      started logman.exe.
      """
      #UNIQUE_PROCESS_KEY = 19
      #PROCESS_ID = 20
      PARENT_PID = 21
      #SESSION_ID = 22
      #EXIT_STATUS = 23
      #DIRECTORY_TABLE_BASE = 24
      #USER_SID = 25
      IMAGE_FILE_NAME = 26
      #COMMAND_LINE = 27

      ppid = int(line[PARENT_PID], 16)
      if line[IMAGE_FILE_NAME] == '"logman.exe"':
        # logman's parent is trace_input.py or whatever tool using it as a
        # library. Trace any other children started by it.
        assert ppid not in self.processes
        self.processes[ppid] = self.Process(self, ppid, None, None)
        logging.info('Found logman\'s parent at %d' % ppid)

    def handle_Process_End(self, line):
      # Look if it is logman terminating, if so, grab the parent's process pid
      # and inject cwd.
      pid = line[self.PID]
      if pid in self.processes:
        logging.info('Terminated: %d' % pid)
        self.processes[pid].cwd = None

    def handle_Process_Start(self, line):
      """Handles a new child process started by PID."""
      #UNIQUE_PROCESS_KEY = 19
      PROCESS_ID = 20
      #PARENT_PID = 21
      #SESSION_ID = 22
      #EXIT_STATUS = 23
      #DIRECTORY_TABLE_BASE = 24
      #USER_SID = 25
      IMAGE_FILE_NAME = 26
      #COMMAND_LINE = 27

      ppid = line[self.PID]
      pid = int(line[PROCESS_ID], 16)
      if ppid in self.processes:
        # Need to ignore processes we don't know about because the log is
        # system-wide.
        if line[IMAGE_FILE_NAME] == '"logman.exe"':
          # Skip the shutdown call when "logman.exe stop" is executed.
          return
        assert pid not in self.processes
        self.processes[pid] = self.Process(self, pid, None, ppid)
        logging.info(
            'New child: %d -> %d %s' % (ppid, pid, line[IMAGE_FILE_NAME]))

    def handle_Thread_End(self, line):
      """Has the same parameters as Thread_Start."""
      tid = int(line[self.TID], 16)
      self._threads_active.pop(tid, None)

    def handle_Thread_Start(self, line):
      """Handles a new thread created.

      Do not use self.PID here since a process' initial thread is created by
      the parent process.
      """
      PROCESS_ID = 19
      TTHREAD_ID = 20
      #STACK_BASE = 21
      #STACK_LIMIT = 22
      #USER_STACK_BASE = 23
      #USER_STACK_LIMIT = 24
      #AFFINITY = 25
      #WIN32_START_ADDR = 26
      #TEB_BASE = 27
      #SUB_PROCESS_TAG = 28
      #BASE_PRIORITY = 29
      #PAGE_PRIORITY = 30
      #IO_PRIORITY = 31
      #THREAD_FLAGS = 32
      # Do not use self.PID here since a process' initial thread is created by
      # the parent process.
      pid = int(line[PROCESS_ID], 16)
      tid = int(line[TTHREAD_ID], 16)
      self._threads_active[tid] = pid

    def handle_Thread_Any(self, line):
      pass

    def handle_SystemConfig_Any(self, line):
      """If you have too many of these, check your hardware."""
      pass

  def __init__(self):
    super(LogmanTrace, self).__init__()
    # Most ignores need to be determined at runtime.
    self.IGNORED = set([os.path.dirname(sys.executable)])
    # Add many directories from environment variables.
    vars_to_ignore = (
      'APPDATA',
      'LOCALAPPDATA',
      'ProgramData',
      'ProgramFiles',
      'ProgramFiles(x86)',
      'ProgramW6432',
      'SystemRoot',
      'TEMP',
      'TMP',
    )
    for i in vars_to_ignore:
      if os.environ.get(i):
        self.IGNORED.add(os.environ[i])

    # Also add their short path name equivalents.
    for i in list(self.IGNORED):
      self.IGNORED.add(GetShortPathName(i.replace('/', os.path.sep)))

    # Add this one last since it has no short path name equivalent.
    self.IGNORED.add('\\SystemRoot')
    self.IGNORED = tuple(sorted(self.IGNORED))

  @staticmethod
  def clean_trace(logname):
    if os.path.isfile(logname):
      os.remove(logname)
    if os.path.isfile(logname + '.etl'):
      os.remove(logname + '.etl')

  @classmethod
  def _start_log(cls, etl):
    """Starts the log collection.

    Requires administrative access. logman.exe is synchronous so no need for a
    "warmup" call.  'Windows Kernel Trace' is *localized* so use its GUID
    instead.  The GUID constant name is SystemTraceControlGuid. Lovely.

    One can get the list of potentially interesting providers with:
    "logman query providers | findstr /i file"
    """
    cmd_start = [
      'logman.exe',
      'start',
      'NT Kernel Logger',
      '-p', '{9e814aad-3204-11d2-9a82-006008a86939}',
      # splitio,fileiocompletion,syscall,file,cswitch,img
      '(process,fileio,thread)',
      '-o', etl,
      '-ets',  # Send directly to kernel
      # Values extracted out of thin air.
      '-bs', '1024',
      '-nb', '200', '512',
    ]
    logging.debug('Running: %s' % cmd_start)
    try:
      subprocess.check_call(
          cmd_start,
          stdin=subprocess.PIPE,
          stdout=subprocess.PIPE,
          stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError, e:
      if e.returncode == -2147024891:
        print >> sys.stderr, 'Please restart with an elevated admin prompt'
      elif e.returncode == -2144337737:
        print >> sys.stderr, (
            'A kernel trace was already running, stop it and try again')
      raise

  @staticmethod
  def _stop_log():
    """Stops the kernel log collection."""
    cmd_stop = [
      'logman.exe',
      'stop',
      'NT Kernel Logger',
      '-ets',  # Sends the command directly to the kernel.
    ]
    logging.debug('Running: %s' % cmd_stop)
    subprocess.check_call(
        cmd_stop,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT)

  @classmethod
  def gen_trace(cls, cmd, cwd, logname, output):
    """Uses logman.exe to start and stop the NT Kernel Logger while the
    executable to be traced is run.
    """
    logging.info('gen_trace(%s, %s, %s, %s)' % (cmd, cwd, logname, output))
    # Use "logman -?" for help.

    etl = logname + '.etl'

    stdout = stderr = None
    if output:
      stdout = subprocess.PIPE
      stderr = subprocess.STDOUT

    # 1. Start the log collection.
    cls._start_log(etl)

    # 2. Run the child process.
    logging.debug('Running: %s' % cmd)
    try:
      child = subprocess.Popen(
          cmd, cwd=cwd, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
      out = child.communicate()[0]
    finally:
      # 3. Stop the log collection.
      cls._stop_log()

    # 4. Convert the traces to text representation.
    # Use "tracerpt -?" for help.
    LOCALE_INVARIANT = 0x7F
    windll.kernel32.SetThreadLocale(LOCALE_INVARIANT)
    cmd_convert = [
      'tracerpt.exe',
      '-l', etl,
      '-o', logname,
      '-gmt',  # Use UTC
      '-y',  # No prompt
      # Use -of XML to get the header of each items after column 19, e.g. all
      # the actual headers of 'User Data'.
    ]

    # Normally, 'csv' is sufficient. If complex scripts are used (like eastern
    # languages), use 'csv_unicode'. If localization gets in the way, use 'xml'.
    logformat = 'csv'

    if logformat == 'csv':
      # tracerpt localizes the 'Type' column, for major brainfuck
      # entertainment. I can't imagine any sane reason to do that.
      cmd_convert.extend(['-of', 'CSV'])
    elif logformat == 'csv_utf16':
      # This causes it to use UTF-16, which doubles the log size but ensures the
      # log is readable for non-ASCII characters.
      cmd_convert.extend(['-of', 'CSV', '-en', 'Unicode'])
    elif logformat == 'xml':
      cmd_convert.extend(['-of', 'XML'])
    else:
      assert False, logformat
    logging.debug('Running: %s' % cmd_convert)
    subprocess.check_call(
        cmd_convert, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)

    return child.returncode, out

  @classmethod
  def parse_log(cls, filename, blacklist):
    logging.info('parse_log(%s, %s)' % (filename, blacklist))

    # Auto-detect the log format.
    with open(filename, 'rb') as f:
      hdr = f.read(2)
      assert len(hdr) == 2
      if hdr == '<E':
        # It starts with <Events>.
        logformat = 'xml'
      elif hdr == '\xFF\xEF':
        # utf-16 BOM.
        logformat = 'csv_utf16'
      else:
        logformat = 'csv'

    context = cls.Context(blacklist)

    if logformat == 'csv_utf16':
      def utf_8_encoder(unicode_csv_data):
        """Encodes the unicode object as utf-8 encoded str instance"""
        for line in unicode_csv_data:
          yield line.encode('utf-8')

      def unicode_csv_reader(unicode_csv_data, **kwargs):
        """Encodes temporarily as UTF-8 since csv module doesn't do unicode."""
        csv_reader = csv.reader(utf_8_encoder(unicode_csv_data), **kwargs)
        for row in csv_reader:
          # Decode str utf-8 instances back to unicode instances, cell by cell:
          yield [cell.decode('utf-8') for cell in row]

      # The CSV file is UTF-16 so use codecs.open() to load the file into the
      # python internal unicode format (utf-8). Then explicitly re-encode as
      # utf8 as str instances so csv can parse it fine. Then decode the utf-8
      # str back into python unicode instances. This sounds about right.
      for line in unicode_csv_reader(codecs.open(filename, 'r', 'utf-16')):
        # line is a list of unicode objects
        context.on_csv_line(line)

    elif logformat == 'csv':
      def ansi_csv_reader(ansi_csv_data, **kwargs):
        """Loads an 'ANSI' code page and returns unicode() objects."""
        assert sys.getfilesystemencoding() == 'mbcs'
        encoding = get_current_encoding()
        for row in csv.reader(ansi_csv_data, **kwargs):
          # Decode str 'ansi' instances to unicode instances, cell by cell:
          yield [cell.decode(encoding) for cell in row]

      # The fastest and smallest format but only supports 'ANSI' file paths.
      # E.g. the filenames are encoding in the 'current' encoding.
      for line in ansi_csv_reader(open(filename)):
        # line is a list of unicode objects.
        context.on_csv_line(line)

    else:
      raise NotImplementedError('Implement %s' % logformat)

    files, non_existent = context.resolve()
    # Resolve any symlink we hit.
    return (
        set(os.path.realpath(f) for f in files),
        set(os.path.realpath(f) for f in non_existent),
        len(context.processes))


def relevant_files(files, root):
  """Trims the list of files to keep the expected files and unexpected files.

  Unexpected files are files that are not based inside the |root| directory.
  """
  expected = []
  unexpected = []
  for f in files:
    if f.startswith(root):
      f = f[len(root):]
      assert f
      expected.append(f)
    else:
      unexpected.append(f)
  return sorted(set(expected)), sorted(set(unexpected))


def extract_directories(files, root):
  """Detects if all the files in a directory were loaded and if so, replace the
  individual files by the directory entry.
  """
  directories = set(os.path.dirname(f) for f in files)
  files = set(files)
  for directory in sorted(directories, reverse=True):
    actual = set(
      os.path.join(directory, f) for f in
      os.listdir(os.path.join(root, directory))
      if not f.endswith(('.svn', '.pyc'))
    )
    if not (actual - files):
      files -= actual
      files.add(directory + os.path.sep)
  return sorted(files)


def pretty_print(variables, stdout):
  """Outputs a gyp compatible list from the decoded variables.

  Similar to pprint.print() but with NIH syndrome.
  """
  # Order the dictionary keys by these keys in priority.
  ORDER = (
      'variables', 'condition', 'command', 'relative_cwd', 'read_only',
      KEY_TRACKED, KEY_UNTRACKED)

  def sorting_key(x):
    """Gives priority to 'most important' keys before the others."""
    if x in ORDER:
      return str(ORDER.index(x))
    return x

  def loop_list(indent, items):
    for item in items:
      if isinstance(item, basestring):
        stdout.write('%s\'%s\',\n' % (indent, item))
      elif isinstance(item, dict):
        stdout.write('%s{\n' % indent)
        loop_dict(indent + '  ', item)
        stdout.write('%s},\n' % indent)
      elif isinstance(item, list):
        # A list inside a list will write the first item embedded.
        stdout.write('%s[' % indent)
        for index, i in enumerate(item):
          if isinstance(i, basestring):
            stdout.write(
                '\'%s\', ' % i.replace('\\', '\\\\').replace('\'', '\\\''))
          elif isinstance(i, dict):
            stdout.write('{\n')
            loop_dict(indent + '  ', i)
            if index != len(item) - 1:
              x = ', '
            else:
              x = ''
            stdout.write('%s}%s' % (indent, x))
          else:
            assert False
        stdout.write('],\n')
      else:
        assert False

  def loop_dict(indent, items):
    for key in sorted(items, key=sorting_key):
      item = items[key]
      stdout.write("%s'%s': " % (indent, key))
      if isinstance(item, dict):
        stdout.write('{\n')
        loop_dict(indent + '  ', item)
        stdout.write(indent + '},\n')
      elif isinstance(item, list):
        stdout.write('[\n')
        loop_list(indent + '  ', item)
        stdout.write(indent + '],\n')
      elif isinstance(item, basestring):
        stdout.write(
            '\'%s\',\n' % item.replace('\\', '\\\\').replace('\'', '\\\''))
      elif item in (True, False, None):
        stdout.write('%s\n' % item)
      else:
        assert False, item

  stdout.write('{\n')
  loop_dict('  ', variables)
  stdout.write('}\n')


def get_api():
  flavor = get_flavor()
  if flavor == 'linux':
    return Strace()
  elif flavor == 'mac':
    return Dtrace()
  elif sys.platform == 'win32':
    return LogmanTrace()
  else:
    print >> sys.stderr, 'Unsupported platform %s' % sys.platform
    sys.exit(1)


def get_blacklist(api):
  """Returns a function to filter unimportant files normally ignored."""
  git_path = os.path.sep + '.git' + os.path.sep
  svn_path = os.path.sep + '.svn' + os.path.sep
  return lambda f: (
      f.startswith(api.IGNORED) or
      f.endswith('.pyc') or
      git_path in f or
      svn_path in f)


def generate_dict(files, cwd_dir, product_dir):
  """Converts the list of files into a .isolate dictionary.

  Arguments:
  - files: list of files to generate a dictionary out of.
  - cwd_dir: directory to base all the files from, relative to root_dir.
  - product_dir: directory to replace with <(PRODUCT_DIR), relative to root_dir.
  """
  cwd_dir = cleanup_path(cwd_dir)
  product_dir = cleanup_path(product_dir)

  def fix(f):
    """Bases the file on the most restrictive variable."""
    logging.debug('fix(%s)' % f)
    # Important, GYP stores the files with / and not \.
    f = f.replace(os.path.sep, '/')
    if product_dir and f.startswith(product_dir):
      return '<(PRODUCT_DIR)/%s' % f[len(product_dir):]
    else:
      # cwd_dir is usually the directory containing the gyp file. It may be
      # empty if the whole directory containing the gyp file is needed.
      return posix_relpath(f, cwd_dir) or './'

  corrected = [fix(f) for f in files]
  tracked = [f for f in corrected if not f.endswith('/') and ' ' not in f]
  untracked = [f for f in corrected if f.endswith('/') or ' ' in f]
  variables = {}
  if tracked:
    variables[KEY_TRACKED] = tracked
  if untracked:
    variables[KEY_UNTRACKED] = untracked
  return variables


def trace(logfile, cmd, cwd, api, output):
  """Traces an executable. Returns (returncode, output) from api.

  Arguments:
  - logfile: file to write to.
  - cmd: command to run.
  - cwd: current directory to start the process in.
  - api: a tracing api instance.
  - output: if True, returns output, otherwise prints it at the console.
  """
  cmd = fix_python_path(cmd)
  assert os.path.isabs(cmd[0]), cmd[0]
  api.clean_trace(logfile)
  return api.gen_trace(cmd, cwd, logfile, output)


def load_trace(logfile, root_dir, api):
  """Loads a trace file and returns the processed file lists.

  Arguments:
  - logfile: file to load.
  - root_dir: root directory to use to determine if a file is relevant to the
              trace or not.
  - api: a tracing api instance.
  """
  root_dir = get_native_path_case(root_dir)
  files, non_existent, processes = api.parse_log(logfile, get_blacklist(api))
  expected, unexpected = relevant_files(
      files, root_dir.rstrip(os.path.sep) + os.path.sep)
  # In case the file system is case insensitive.
  expected = sorted(set(
      get_native_path_case(os.path.join(root_dir, f))[len(root_dir)+1:]
      for f in expected))
  simplified = extract_directories(expected, root_dir)
  return files, expected, unexpected, non_existent, simplified, processes


def trace_inputs(logfile, cmd, root_dir, cwd_dir, product_dir, force_trace):
  """Tries to load the logs if available. If not, trace the test.

  Symlinks are not processed at all.

  Arguments:
  - logfile:     Absolute path to the OS-specific trace.
  - cmd:         Command list to run.
  - root_dir:    Base directory where the files we care about live.
  - cwd_dir:     Cwd to use to start the process, relative to the root_dir
                 directory.
  - product_dir: Directory containing the executables built by the build
                 process, relative to the root_dir directory. It is used to
                 properly replace paths with <(PRODUCT_DIR) for gyp output.
  - force_trace: Will force to trace unconditionally even if a trace already
                 exist.
  """
  logging.debug(
      'trace_inputs(%s, %s, %s, %s, %s, %s)' % (
        logfile, cmd, root_dir, cwd_dir, product_dir, force_trace))

  def print_if(txt):
    if cwd_dir is None:
      print txt

  # It is important to have unambiguous path.
  assert os.path.isabs(root_dir), root_dir
  assert os.path.isabs(logfile), logfile
  assert not cwd_dir or not os.path.isabs(cwd_dir), cwd_dir
  assert not product_dir or not os.path.isabs(product_dir), product_dir

  api = get_api()
  # Resolve any symlink
  root_dir = os.path.realpath(root_dir)
  if not os.path.isfile(logfile) or force_trace:
    print_if('Tracing... %s' % cmd)
    # Use the proper relative directory.
    cwd = root_dir if not cwd_dir else os.path.join(root_dir, cwd_dir)
    silent = not isEnabledFor(logging.WARNING)
    returncode, _ = trace(logfile, cmd, cwd, api, silent)
    if returncode and not force_trace:
      return returncode

  print_if('Loading traces... %s' % logfile)
  files, expected, unexpected, non_existent, simplified, _ = load_trace(
      logfile, root_dir, api)

  print_if('Total: %d' % len(files))
  print_if('Non existent: %d' % len(non_existent))
  for f in non_existent:
    print_if('  %s' % f)
  if unexpected:
    print_if('Unexpected: %d' % len(unexpected))
    for f in unexpected:
      print_if('  %s' % f)
  print_if('Interesting: %d reduced to %d' % (len(expected), len(simplified)))
  for f in simplified:
    print_if('  %s' % f)

  if cwd_dir is not None:
    value = {
      'conditions': [
        ['OS=="%s"' % get_flavor(), {
          'variables': generate_dict(simplified, cwd_dir, product_dir),
        }],
      ],
    }
    pretty_print(value, sys.stdout)
  return 0


def main():
  parser = optparse.OptionParser(
      usage='%prog <options> [cmd line...]')
  parser.allow_interspersed_args = False
  parser.add_option(
      '-v', '--verbose', action='count', default=0, help='Use multiple times')
  parser.add_option('-l', '--log', help='Log file')
  parser.add_option(
      '-c', '--cwd',
      help='Signal to start the process from this relative directory. When '
           'specified, outputs the inputs files in a way compatible for '
           'gyp processing. Should be set to the relative path containing the '
           'gyp file, e.g. \'chrome\' or \'net\'')
  parser.add_option(
      '-p', '--product-dir', default='out/Release',
      help='Directory for PRODUCT_DIR. Default: %default')
  parser.add_option(
      '--root-dir', default=ROOT_DIR,
      help='Root directory to base everything off. Default: %default')
  parser.add_option(
      '-f', '--force',
      action='store_true',
      default=False,
      help='Force to retrace the file')

  options, args = parser.parse_args()
  level = [logging.ERROR, logging.INFO, logging.DEBUG][min(2, options.verbose)]
  logging.basicConfig(
        level=level,
        format='%(levelname)5s %(module)15s(%(lineno)3d):%(message)s')

  if not options.log:
    parser.error('Must supply a log file with -l')
  if not args:
    if not os.path.isfile(options.log) or options.force:
      parser.error('Must supply a command to run')
  else:
    args[0] = os.path.abspath(args[0])

  if options.root_dir:
    options.root_dir = os.path.abspath(options.root_dir)

  return trace_inputs(
      os.path.abspath(options.log),
      args,
      options.root_dir,
      options.cwd,
      options.product_dir,
      options.force)


if __name__ == '__main__':
  sys.exit(main())
