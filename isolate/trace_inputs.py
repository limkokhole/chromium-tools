#!/usr/bin/env python
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Runs strace or dtrace on a test and processes the logs to extract the
dependencies from the source tree.

Automatically extracts directories where all the files are used to make the
dependencies list more compact.
"""

import logging
import optparse
import os
import re
import subprocess
import sys


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(os.path.dirname(BASE_DIR))


def isEnabledFor(level):
  return logging.getLogger().isEnabledFor(level)


class Strace(object):
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

  class _Context(object):
    """Processes a strace log line and keeps the list of existent and non
    existent files accessed.

    Ignores directories.
    """
    # This is the most common format. pid function(args) = result
    RE_HEADER = re.compile(r'^(\d+)\s+([^\(]+)\((.+?)\)\s+= (.+)$')
    # An interrupted function call, only grab the minimal header.
    RE_UNFINISHED = re.compile(r'^(\d+)\s+([^\(]+).*$')
    UNFINISHED = ' <unfinished ...>'
    # A resumed function call.
    RE_RESUMED = re.compile(r'^(\d+)\s+<\.\.\. ([^ ]+) resumed> (.+)$')
    # A process received a signal.
    RE_SIGNAL = re.compile(r'^\d+\s+--- SIG[A-Z]+ .+ ---')
    # A process didn't handle a signal.
    RE_KILLED = re.compile(r'^(\d+) \+\+\+ killed by ([A-Z]+) \+\+\+$')

    # Arguments parsing.
    RE_CHDIR = re.compile(r'^\"(.+?)\"$')
    RE_EXECVE = re.compile(r'^\"(.+?)\", \[.+?\], \[.+?\]$')
    RE_OPEN2 = re.compile(r'^\"(.*?)\", ([A-Z\_\|]+)$')
    RE_OPEN3 = re.compile(r'^\"(.*?)\", ([A-Z\_\|]+), (\d+)$')
    RE_RENAME = re.compile(r'^\"(.+?)\", \"(.+?)\"$')

    def __init__(self, blacklist):
      self._cwd = {}
      self.blacklist = blacklist
      self.files = set()
      self.non_existent = set()
      # Key is a tuple(pid, function name)
      self._pending_calls = {}

    @classmethod
    def traces(cls):
      prefix = 'handle_'
      return [i[len(prefix):] for i in dir(cls) if i.startswith(prefix)]

    def on_line(self, line):
      line = line.strip()
      if self.RE_SIGNAL.match(line):
        # Ignore signals.
        return

      m = self.RE_KILLED.match(line)
      if m:
        self.handle_exit_group(int(m.group(1)), m.group(2), None, None)
        return

      if line.endswith(self.UNFINISHED):
        line = line[:-len(self.UNFINISHED)]
        m = self.RE_UNFINISHED.match(line)
        assert m, line
        self._pending_calls[(m.group(1), m.group(2))] = line
        return

      m = self.RE_RESUMED.match(line)
      if m:
        pending = self._pending_calls.pop((m.group(1), m.group(2)))
        # Reconstruct the line.
        line = pending + m.group(3)

      m = self.RE_HEADER.match(line)
      assert m, line
      return getattr(self, 'handle_%s' % m.group(2))(
          int(m.group(1)),
          m.group(2),
          m.group(3),
          m.group(4))

    def handle_chdir(self, pid, _function, args, result):
      """Updates cwd."""
      if result.startswith('0'):
        cwd = self.RE_CHDIR.match(args).group(1)
        if not cwd.startswith('/'):
          cwd2 = os.path.join(self._cwd[pid], cwd)
          logging.debug('handle_chdir(%d, %s) -> %s' % (pid, cwd, cwd2))
          self._cwd[pid] = cwd2
        else:
          logging.debug('handle_chdir(%d, %s)' % (pid, cwd))
          self._cwd[pid] = cwd
      else:
        assert False, 'Unexecpected fail: %s' % result

    def handle_clone(self, pid, _function, _args, result):
      """Transfers cwd."""
      if result == '? ERESTARTNOINTR (To be restarted)':
        return
      self._cwd[int(result)] = self._cwd[pid]

    def handle_execve(self, pid, _function, args, result):
      self._handle_file(pid, self.RE_EXECVE.match(args).group(1), result)

    def handle_exit_group(self, pid, _function, _args, _result):
      """Removes cwd."""
      del self._cwd[pid]

    @staticmethod
    def handle_fork(_pid, _function, args, result):
      assert False, (args, result)

    def handle_open(self, pid, _function, args, result):
      args = (self.RE_OPEN3.match(args) or self.RE_OPEN2.match(args)).groups()
      if 'O_DIRECTORY' in args[1]:
        return
      self._handle_file(pid, args[0], result)

    def handle_rename(self, pid, _function, args, result):
      args = self.RE_RENAME.match(args).groups()
      self._handle_file(pid, args[0], result)
      self._handle_file(pid, args[1], result)

    @staticmethod
    def handle_stat64(_pid, _function, args, result):
      assert False, (args, result)

    @staticmethod
    def handle_vfork(_pid, _function, args, result):
      assert False, (args, result)

    def _handle_file(self, pid, filepath, result):
      if result.startswith('-1'):
        return
      if not filepath.startswith('/'):
        filepath2 = os.path.join(self._cwd[pid], filepath)
        logging.debug('_handle_file(%d, %s) -> %s' % (pid, filepath, filepath2))
        filepath = filepath2
      else:
        logging.debug('_handle_file(%d, %s)' % (pid, filepath))

      if self.blacklist(filepath):
        return
      if filepath not in self.files and filepath not in self.non_existent:
        if os.path.isfile(filepath):
          self.files.add(filepath)
        else:
          self.non_existent.add(filepath)

  @classmethod
  def gen_trace(cls, cmd, cwd, logname):
    """Runs strace on an executable."""
    logging.info('gen_trace(%s, %s, %s)' % (cmd, cwd, logname))
    silent = not isEnabledFor(logging.INFO)
    stdout = stderr = None
    if silent:
      stdout = subprocess.PIPE
      stderr = subprocess.PIPE
    traces = ','.join(cls._Context.traces())
    trace_cmd = ['strace', '-f', '-e', 'trace=%s' % traces, '-o', logname]
    p = subprocess.Popen(
        trace_cmd + cmd, cwd=cwd, stdout=stdout, stderr=stderr)
    out, err = p.communicate()
    # Once it's done, inject a chdir() call to cwd to be able to reconstruct
    # the full paths.
    # TODO(maruel): cwd should be saved at each process creation, so forks needs
    # to be traced properly.
    if os.path.isfile(logname):
      with open(logname) as f:
        content = f.read()
      with open(logname, 'w') as f:
        pid = content.split(' ', 1)[0]
        f.write('%s chdir("%s") = 0\n' % (pid, cwd))
        f.write(content)

    if p.returncode != 0:
      print 'Failure: %d' % p.returncode
      # pylint: disable=E1103
      if out:
        print ''.join(out.splitlines(True)[-100:])
      if err:
        print ''.join(err.splitlines(True)[-100:])
    return p.returncode

  @classmethod
  def parse_log(cls, filename, blacklist):
    """Processes a strace log and returns the files opened and the files that do
    not exist.

    It does not track directories.

    Most of the time, files that do not exist are temporary test files that
    should be put in /tmp instead. See http://crbug.com/116251
    """
    logging.info('parse_log(%s, %s)' % (filename, blacklist))
    context = cls._Context(blacklist)
    for line in open(filename):
      context.on_line(line)
    # Resolve any symlink we hit.
    return (
        set(os.path.realpath(f) for f in context.files),
        set(os.path.realpath(f) for f in context.non_existent))


class Dtrace(object):
  """Uses DTrace framework through dtrace. Requires root access.

  Implies Mac OSX.

  dtruss can't be used because it has compatibility issues with python.
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
  # and
  # https://wikis.oracle.com/display/DTrace/Variables
  # Note that cwd doesn't keep the absolute path so we need to compute it
  # ourselve!
  D_CODE = """
      /* Child process tracking.
       * I'm really depressed that I need to do it myself. */
      dtrace:::BEGIN {
        trackedpid[ppid] = 1;
        trackedpid[pid] = 1;
        /* Signal gen_trace() that we are ready to trace. */
        printf("Go! 1 %d:%d %s", ppid, pid, execname);
      }

      /* Make sure all child processes are tracked. This is not very efficient
       * but for our use case, it's fine enough.
       * TODO(maruel): We should properly track fork, execve, vfork and friends
       * instead. */
      syscall:::entry /trackedpid[ppid]/ {
        trackedpid[pid] = 1;
      }
      syscall::exit:entry /trackedpid[pid]/ {
        trackedpid[pid] = 0;
      }

      /* Finally what we care about! */
      syscall::open*:entry /trackedpid[pid]/ {
        self->arg0 = copyinstr(arg0);
        self->arg1 = arg1;
        self->arg2 = arg2;
        self->cwd = cwd;
      }
      syscall::open*:return /trackedpid[pid]/ {
        printf("%d:%d \\"%s\\"; \\"%s\\"; \\"%d\\"; \\"%d\\" = %d",
               ppid, pid, execname, self->arg0, self->arg1, self->arg2, errno);
        self->arg0 = 0;
        self->arg1 = 0;
        self->arg2 = 0;
      }
      /* Track chdir, it's painful because it is only receiving relative path */
      syscall::chdir:entry /trackedpid[pid]/ {
        self->arg0 = copyinstr(arg0);
      }
      syscall::chdir:return /trackedpid[pid] && errno == 0/ {
        printf("%d:%d \\"%s\\"; \\"%s\\" = %d",
            ppid, pid, execname, self->arg0, errno);
        self->cwd = self->arg0;
      }
      """

  @classmethod
  def gen_trace(cls, cmd, cwd, logname):
    """Runs dtrace on an executable."""
    logging.info('gen_trace(%s, %s, %s)' % (cmd, cwd, logname))
    silent = not isEnabledFor(logging.INFO)
    logging.info('Running: %s' % cmd)
    signal = 'Go!'
    logging.debug('Our pid: %d' % os.getpid())

    # Part 1: start the child process.
    stdout = stderr = None
    if silent:
      stdout = subprocess.PIPE
      stderr = subprocess.PIPE
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
    trace_cmd = [
      'sudo',
      'dtrace',
      '-x', 'dynvarsize=4m',
      '-x', 'evaltime=exec',
      '-n', cls.D_CODE,
      '-o', '/dev/stderr',
      '-p', str(child.pid),
    ]
    with open(logname, 'w') as logfile:
      # TODO(maruel): Inject a chdir() call with the absolute path (!) of cwd to
      # be able to reconstruct the full paths.
      #logfile.write('0 chdir("%s") = 0\n' % cwd)
      #logfile.flush()
      dtrace = subprocess.Popen(
          trace_cmd, stdout=logfile, stderr=subprocess.STDOUT)
    logging.debug('Started dtrace pid: %d' % dtrace.pid)

    # Part 3: Read until the Go! signal is sent.
    with open(logname, 'r') as logfile:
      while True:
        x = logfile.readline()
        if signal in x:
          break

    # Part 4: We can now tell our child to go.
    child.communicate(signal)

    dtrace.wait()
    if dtrace.returncode != 0:
      print 'Failure: %d' % dtrace.returncode
      with open(logname) as logfile:
        print ''.join(logfile.readlines()[-100:])
      # Find a better way.
      os.remove(logname)
    return dtrace.returncode

  @staticmethod
  def parse_log(filename, blacklist):
    """Processes a dtrace log and returns the files opened and the files that do
    not exist.

    Most of the time, files that do not exist are temporary test files that
    should be put in /tmp instead. See http://crbug.com/116251

    TODO(maruel): Process chdir() calls so relative paths can be processed.
    """
    logging.info('parse_log(%s, %s)' % (filename, blacklist))
    files = set()
    non_existent = set()
    ignored = set()
    # 1=filepath, 2=flags, 3=mode, 4=cwd 5=result
    re_open = re.compile(
        #     CPU   ID    PROBE            PPID PID EXECNAME
        r'^\s+\d+\s+\d+\s+open[^\:]+:return \d+:\d+ \"[^\"]+\"; '
        # PATH          FLAGS         MODE            RESULT
        r'\"([^\"]+)\"; \"([^\"]+)\"; \"([^\"]+)\" \= (.+)$')
    # TODO(maruel): Handling chdir() and cwd in general on OSX is complex
    # because OSX only keeps relative directory names. In addition, cwd is a
    # process local variable so forks need to be properly traced and cwd saved.
    for line in open(filename):
      m = re_open.match(line)
      if not m:
        continue
      if not m.group(4).startswith('0'):
        # Called failed.
        continue
      filepath = m.group(1)
      if not filepath.startswith('/'):
        # TODO(maruel): This is wrong since the cwd dtrace variable on OSX is
        # not an absolute path.
        # filepath = os.path.join(m.group(cwd), filepath)
        pass
      if blacklist(filepath):
        continue
      if (filepath not in files and
          filepath not in non_existent and
          filepath not in ignored):
        if os.path.isfile(filepath):
          files.add(filepath)
        elif not os.path.isdir(filepath):
          # Silently ignore directories.
          non_existent.add(filepath)
        else:
          ignored.add(filepath)
    return files, non_existent


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
      files.add(directory + '/')
  return sorted(files)


def trace_inputs(
    logfile, cmd, root_dir, gyp_proj_dir, product_dir, force_trace):
  """Tries to load the logs if available. If not, trace the test.

  Symlinks are not processed at all.
  """
  logging.debug(
      'trace_inputs(%s, %s, %s, %s, %s, %s)' % (
        logfile, cmd, root_dir, gyp_proj_dir, product_dir, force_trace))

  # It is important to have unambiguous path.
  assert os.path.isabs(root_dir), root_dir
  assert os.path.isabs(logfile), logfile
  assert (
      (os.path.isfile(logfile) and not force_trace) or os.path.isabs(cmd[0])
      ), cmd[0]
  # Resolve any symlink
  root_dir = os.path.realpath(root_dir)

  def print_if(txt):
    if gyp_proj_dir is None:
      print(txt)

  if sys.platform == 'linux2':
    api = Strace()
  elif sys.platform == 'darwin':
    api = Dtrace()
  else:
    print >> sys.stderr, 'Unsupported platform'
    return 1

  if not os.path.isfile(logfile) or force_trace:
    if os.path.isfile(logfile):
      os.remove(logfile)
    print_if('Tracing... %s' % cmd)
    cwd = root_dir
    # TODO(maruel): If --gyp is specified, use it as the cwd.
    #if gyp_proj_dir:
    #  cwd = os.path.join(cwd, gyp_proj_dir)
    returncode = api.gen_trace(cmd, cwd, logfile)
    if returncode and not force_trace:
      return returncode

  def blacklist(f):
    """Strips ignored paths."""
    return f.startswith(api.IGNORED) or f.endswith('.pyc')

  print_if('Loading traces... %s' % logfile)
  files, non_existent = api.parse_log(logfile, blacklist)

  print_if('Total: %d' % len(files))
  print_if('Non existent: %d' % len(non_existent))
  for f in non_existent:
    print_if('  %s' % f)

  expected, unexpected = relevant_files(files, root_dir.rstrip('/') + '/')
  if unexpected:
    print_if('Unexpected: %d' % len(unexpected))
    for f in unexpected:
      print_if('  %s' % f)

  simplified = extract_directories(expected, root_dir)
  print_if('Interesting: %d reduced to %d' % (len(expected), len(simplified)))
  for f in simplified:
    print_if('  %s' % f)

  if gyp_proj_dir is not None:
    def cleanuppath(x):
      """Cleans up a relative path."""
      if x:
        x = x.rstrip('/')
      if x == '.':
        x = ''
      if x:
        x += '/'
      return x

    gyp_proj_dir = cleanuppath(gyp_proj_dir)
    product_dir = cleanuppath(product_dir)

    def fix(f):
      """Bases the file on the most restrictive variable."""
      logging.debug('fix(%s)' % f)
      if product_dir and f.startswith(product_dir):
        return '<(PRODUCT_DIR)/%s' % f[len(product_dir):]
      elif gyp_proj_dir and f.startswith(gyp_proj_dir):
        # May be empty if the whole directory containing the gyp file is needed.
        return f[len(gyp_proj_dir):] or './'
      else:
        return '<(DEPTH)/%s' % f

    corrected = [fix(f) for f in simplified]
    files = [f for f in corrected if not f.endswith('/')]
    dirs = [f for f in corrected if f.endswith('/')]
    # Constructs the python code manually.
    print(
        '{\n'
        '  \'variables\': {\n'
        '    \'isolate_files\': [\n') + (
            ''.join('      \'%s\',\n' % f for f in files)) + (
        '    ],\n'
        '    \'isolate_dirs\': [\n') + (
            ''.join('      \'%s\',\n' % f for f in dirs)) + (
        '    ],\n'
        '  },\n'
        '},')
  return 0


def main():
  parser = optparse.OptionParser(
      usage='%prog <options> [cmd line...]')
  parser.allow_interspersed_args = False
  parser.add_option(
      '-v', '--verbose', action='count', default=0, help='Use multiple times')
  parser.add_option('-l', '--log', help='Log file')
  parser.add_option(
      '-g', '--gyp',
      help='When specified, outputs the inputs files in a way compatible for '
           'gyp processing. Should be set to the relative path containing the '
           'gyp file, e.g. \'chrome\' or \'net\'')
  parser.add_option(
      '-p', '--product-dir', default='out/Release',
      help='Directory for PRODUCT_DIR. Default: %default')
  parser.add_option(
      '--root-dir', default=ROOT_DIR,
      help='Root directory to base everything off. Default: %default')
  parser.add_option(
      '-f', '--force', action='store_true', help='Force to retrace the file')

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
      options.gyp,
      options.product_dir,
      options.force)


if __name__ == '__main__':
  sys.exit(main())
