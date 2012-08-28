#!/usr/bin/env python
# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Reads a manifest, creates a tree of hardlinks and runs the test.

Keeps a local cache.
"""

import ctypes
import json
import logging
import optparse
import os
import Queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib


# Types of action accepted by recreate_tree().
HARDLINK, SYMLINK, COPY = range(1, 4)

RE_IS_SHA1 = re.compile(r'^[a-fA-F0-9]{40}$')


class ConfigError(ValueError):
  """Generic failure to load a manifest."""
  pass


class MappingError(OSError):
  """Failed to recreate the tree."""
  pass


def os_link(source, link_name):
  """Add support for os.link() on Windows."""
  if sys.platform == 'win32':
    if not ctypes.windll.kernel32.CreateHardLinkW(
        unicode(link_name), unicode(source), 0):
      raise OSError()
  else:
    os.link(source, link_name)


def link_file(outfile, infile, action):
  """Links a file. The type of link depends on |action|."""
  logging.debug('Mapping %s to %s' % (infile, outfile))
  if action not in (HARDLINK, SYMLINK, COPY):
    raise ValueError('Unknown mapping action %s' % action)
  if not os.path.isfile(infile):
    raise MappingError('%s is missing' % infile)
  if os.path.isfile(outfile):
    raise MappingError(
        '%s already exist; insize:%d; outsize:%d' %
        (outfile, os.stat(infile).st_size, os.stat(outfile).st_size))

  if action == COPY:
    shutil.copy(infile, outfile)
  elif action == SYMLINK and sys.platform != 'win32':
    # On windows, symlink are converted to hardlink and fails over to copy.
    os.symlink(infile, outfile)
  else:
    try:
      os_link(infile, outfile)
    except OSError:
      # Probably a different file system.
      logging.warn(
          'Failed to hardlink, failing back to copy %s to %s' % (
            infile, outfile))
      shutil.copy(infile, outfile)


def _set_write_bit(path, read_only):
  """Sets or resets the executable bit on a file or directory."""
  mode = os.lstat(path).st_mode
  if read_only:
    mode = mode & 0500
  else:
    mode = mode | 0200
  if hasattr(os, 'lchmod'):
    os.lchmod(path, mode)  # pylint: disable=E1101
  else:
    if stat.S_ISLNK(mode):
      # Skip symlink without lchmod() support.
      logging.debug('Can\'t change +w bit on symlink %s' % path)
      return

    # TODO(maruel): Implement proper DACL modification on Windows.
    os.chmod(path, mode)


def make_writable(root, read_only):
  """Toggle the writable bit on a directory tree."""
  root = os.path.abspath(root)
  for dirpath, dirnames, filenames in os.walk(root, topdown=True):
    for filename in filenames:
      _set_write_bit(os.path.join(dirpath, filename), read_only)

    for dirname in dirnames:
      _set_write_bit(os.path.join(dirpath, dirname), read_only)


def rmtree(root):
  """Wrapper around shutil.rmtree() to retry automatically on Windows."""
  make_writable(root, False)
  if sys.platform == 'win32':
    for i in range(3):
      try:
        shutil.rmtree(root)
        break
      except WindowsError:  # pylint: disable=E0602
        delay = (i+1)*2
        print >> sys.stderr, (
            'The test has subprocess outliving it. Sleep %d seconds.' % delay)
        time.sleep(delay)
  else:
    shutil.rmtree(root)


def is_same_filesystem(path1, path2):
  """Returns True if both paths are on the same filesystem.

  This is required to enable the use of hardlinks.
  """
  assert os.path.isabs(path1), path1
  assert os.path.isabs(path2), path2
  if sys.platform == 'win32':
    # If the drive letter mismatches, assume it's a separate partition.
    # TODO(maruel): It should look at the underlying drive, a drive letter could
    # be a mount point to a directory on another drive.
    assert re.match(r'^[a-zA-Z]\:\\.*', path1), path1
    assert re.match(r'^[a-zA-Z]\:\\.*', path2), path2
    if path1[0].lower() != path2[0].lower():
      return False
  return os.stat(path1).st_dev == os.stat(path2).st_dev


def open_remote(file_or_url):
  """Reads a file or url."""
  if re.match(r'^https?://.+$', file_or_url):
    return urllib.urlopen(file_or_url)
  return open(file_or_url, 'rb')


def get_free_space(path):
  """Returns the number of free bytes."""
  if sys.platform == 'win32':
    free_bytes = ctypes.c_ulonglong(0)
    ctypes.windll.kernel32.GetDiskFreeSpaceExW(
        ctypes.c_wchar_p(path), None, None, ctypes.pointer(free_bytes))
    return free_bytes.value
  f = os.statvfs(path)
  return f.f_bfree * f.f_frsize


def make_temp_dir(prefix, root_dir):
  """Returns a temporary directory on the same file system as root_dir."""
  base_temp_dir = None
  if not is_same_filesystem(root_dir, tempfile.gettempdir()):
    base_temp_dir = os.path.dirname(root_dir)
  return tempfile.mkdtemp(prefix=prefix, dir=base_temp_dir)


def load_manifest(content):
  """Verifies the manifest is valid and loads this object with the json data.
  """
  try:
    data = json.loads(content)
  except ValueError:
    raise ConfigError('Failed to parse: %s...' % content[:100])

  if not isinstance(data, dict):
    raise ConfigError('Expected dict, got %r' % data)

  for key, value in data.iteritems():
    if key == 'command':
      if not isinstance(value, list):
        raise ConfigError('Expected list, got %r' % value)
      for subvalue in value:
        if not isinstance(subvalue, basestring):
          raise ConfigError('Expected string, got %r' % subvalue)

    elif key == 'files':
      if not isinstance(value, dict):
        raise ConfigError('Expected dict, got %r' % value)
      for subkey, subvalue in value.iteritems():
        if not isinstance(subkey, basestring):
          raise ConfigError('Expected string, got %r' % subkey)
        if not isinstance(subvalue, dict):
          raise ConfigError('Expected dict, got %r' % subvalue)
        for subsubkey, subsubvalue in subvalue.iteritems():
          if subsubkey == 'link':
            if not isinstance(subsubvalue, basestring):
              raise ConfigError('Expected string, got %r' % subsubvalue)
          elif subsubkey == 'mode':
            if not isinstance(subsubvalue, int):
              raise ConfigError('Expected int, got %r' % subsubvalue)
          elif subsubkey == 'sha-1':
            if not RE_IS_SHA1.match(subsubvalue):
              raise ConfigError('Expected sha-1, got %r' % subsubvalue)
          elif subsubkey == 'timestamp':
            if not isinstance(subsubvalue, int):
              raise ConfigError('Expected int, got %r' % subsubvalue)
          else:
            raise ConfigError('Unknown key %s' % subsubkey)
        if bool('sha-1' in subvalue) and bool('link' in subvalue):
          raise ConfigError(
              'Did not expect both \'sha-1\' and \'link\', got: %r' % subvalue)

    elif key == 'read_only':
      if not isinstance(value, bool):
        raise ConfigError('Expected bool, got %r' % value)

    elif key == 'relative_cwd':
      if not isinstance(value, basestring):
        raise ConfigError('Expected string, got %r' % value)

    else:
      raise ConfigError('Unknown key %s' % subkey)

  return data


def fix_python_path(cmd):
  """Returns the fixed command line to call the right python executable."""
  out = cmd[:]
  if out[0] == 'python':
    out[0] = sys.executable
  elif out[0].endswith('.py'):
    out.insert(0, sys.executable)
  return out


class Profiler(object):
  def __init__(self, name):
    self.name = name
    self.start_time = None

  def __enter__(self):
    self.start_time = time.time()
    return self

  def __exit__(self, _exc_type, _exec_value, _traceback):
    time_taken = time.time() - self.start_time
    logging.info('Profiling: Section %s took %3.2f seconds',
                 self.name, time_taken)


class Remote(object):
  """Priority based worker queue to fetch files from a content-address server.

  Supports local file system, CIFS or http remotes.

  When the priority of items is equals, works in strict FIFO mode.
  """
  # Initial and maximum number of worker threads.
  INITIAL_WORKERS = 2
  MAX_WORKERS = 16
  # Priorities.
  LOW, MED, HIGH = (1<<8, 2<<8, 3<<8)
  INTERNAL_PRIORITY_BITS = (1<<8) - 1

  def __init__(self, file_or_url):
    # Function to fetch a remote object.
    self._do_item = self._get_remote_fetcher(file_or_url)
    # Contains tuple(priority, index, obj, destination).
    self._queue = Queue.PriorityQueue()
    # Contains tuple(priority, index, obj).
    self._done = Queue.PriorityQueue()

    # To keep FIFO ordering in self._queue. It is assumed xrange's iterator is
    # thread-safe.
    self._next_index = xrange(0, 1<<31).__iter__().next

    # Control access to the following member.
    self._ready_lock = threading.Lock()
    # Number of threads in wait state.
    self._ready = 0

    # Control access to the following member.
    self._workers_lock = threading.Lock()
    self._workers = []
    for _ in range(self.INITIAL_WORKERS):
      self._add_worker()

  def fetch_item(self, priority, obj, dest):
    """Retrieves an object from the remote data store.

    The smaller |priority| gets fetched first.

    Thread-safe.
    """
    assert (priority & self.INTERNAL_PRIORITY_BITS) == 0
    self._fetch(priority, obj, dest)

  def get_result(self):
    """Returns the next file that was successfully fetched."""
    r = self._done.get()
    if r[0] == '-1':
      # It's an exception.
      raise r[2][0], r[2][1], r[2][2]
    return r[2]

  def _fetch(self, priority, obj, dest):
    with self._ready_lock:
      start_new_worker = not self._ready
    self._queue.put((priority, self._next_index(), obj, dest))
    if start_new_worker:
      self._add_worker()

  def _add_worker(self):
    """Add one worker thread if there isn't too many. Thread-safe."""
    with self._workers_lock:
      if len(self._workers) >= self.MAX_WORKERS:
        return False
      worker = threading.Thread(target=self._run)
      self._workers.append(worker)
    worker.daemon = True
    worker.start()

  def _run(self):
    """Worker thread loop."""
    while True:
      try:
        with self._ready_lock:
          self._ready += 1
        item = self._queue.get()
      finally:
        with self._ready_lock:
          self._ready -= 1
      if not item:
        return
      priority, index, obj, dest = item
      try:
        self._do_item(obj, dest)
      except IOError:
        # Retry a few times, lowering the priority.
        if (priority & self.INTERNAL_PRIORITY_BITS) < 5:
          self._fetch(priority + 1, obj, dest)
          continue
        # Transfers the exception back. It has maximum priority.
        self._done.put((-1, 0, sys.exc_info()))
      except:
        # Transfers the exception back. It has maximum priority.
        self._done.put((-1, 0, sys.exc_info()))
      else:
        self._done.put((priority, index, obj))

  @staticmethod
  def _get_remote_fetcher(file_or_url):
    """Returns a object to retrieve objects from a remote."""
    if re.match(r'^https?://.+$', file_or_url):
      file_or_url = file_or_url.rstrip('/') + '/'
      def download_file(item, dest):
        # TODO(maruel): Reuse HTTP connections. The stdlib doesn't make this
        # easy.
        source = file_or_url + item
        logging.debug('download_file(%s, %s)', source, dest)
        urllib.urlretrieve(source, dest)
      return download_file

    def copy_file(item, dest):
      source = os.path.join(file_or_url, item)
      logging.debug('copy_file(%s, %s)', source, dest)
      shutil.copy(source, dest)
    return copy_file


class CachePolicies(object):
  def __init__(self, max_cache_size, min_free_space, max_items):
    """
    Arguments:
    - max_cache_size: Trim if the cache gets larger than this value. If 0, the
                      cache is effectively a leak.
    - min_free_space: Trim if disk free space becomes lower than this value. If
                      0, it unconditionally fill the disk.
    - max_items: Maximum number of items to keep in the cache. If 0, do not
                 enforce a limit.
    """
    self.max_cache_size = max_cache_size
    self.min_free_space = min_free_space
    self.max_items = max_items


class Cache(object):
  """Stateful LRU cache.

  Saves its state as json file.
  """
  STATE_FILE = 'state.json'

  def __init__(self, cache_dir, remote, policies):
    """
    Arguments:
    - cache_dir: Directory where to place the cache.
    - remote: Remote where to fetch items from.
    - policies: cache retention policies.
    """
    self.cache_dir = cache_dir
    self.remote = remote
    self.policies = policies
    self.state_file = os.path.join(cache_dir, self.STATE_FILE)
    # The files are kept as an array in a LRU style. E.g. self.state[0] is the
    # oldest item.
    self.state = []

    # Profiling values.
    # The files added and removed are stored as tuples of the filename and
    # the file size.
    self.files_added = []
    self.files_removed = []
    self.time_retrieving_files = 0

    if not os.path.isdir(self.cache_dir):
      os.makedirs(self.cache_dir)
    if os.path.isfile(self.state_file):
      try:
        self.state = json.load(open(self.state_file, 'r'))
      except (IOError, ValueError), e:
        # Too bad. The file will be overwritten and the cache cleared.
        logging.error(
            'Broken state file %s, ignoring.\n%s' % (self.STATE_FILE, e))
    with Profiler('SetupTrimming'):
      self.trim()

  def __enter__(self):
    return self

  def __exit__(self, _exc_type, _exec_value, _traceback):
    with Profiler('CleanupTrimming'):
      self.trim()

    logging.info('Number of files added to cache: %i',
                 len(self.files_added))
    logging.info('Size of files added to cache: %i',
                 sum(item[1] for item in self.files_added))
    logging.info('Time taken (in seconds) to add files to cache: %s',
                 self.time_retrieving_files)
    logging.debug('All files added:')
    logging.debug(self.files_added)

    logging.info('Number of files removed from cache: %i',
                 len(self.files_removed))
    logging.info('Size of files removed from cache: %i',
                 sum(item[1] for item in self.files_removed))
    logging.debug('All files remove:')
    logging.debug(self.files_added)

  def remove_lru_file(self):
    """Removes the last recently used file."""
    try:
      filename = self.state.pop(0)
      full_path = self.path(filename)
      size = os.stat(full_path).st_size
      logging.info('Trimming %s: %d bytes' % (filename, size))
      self.files_removed.append((filename, size))
      os.remove(full_path)
    except OSError as e:
      logging.error('Error attempting to delete a file\n%s' % e)

  def trim(self):
    """Trims anything we don't know, make sure enough free space exists."""
    # Ensure that all files listed in the state still exist.
    for filename in self.state[:]:
      if not os.path.exists(self.path(filename)):
        logging.info('Removing lost file %s' % filename)
        self.state.remove(filename)

    for filename in os.listdir(self.cache_dir):
      if filename == self.STATE_FILE or filename in self.state:
        continue
      logging.warn('Unknown file %s from cache' % filename)
      # Insert as the oldest file. It will be deleted eventually if not
      # accessed.
      self.state.insert(0, filename)

    # Ensure enough free space.
    while (
        self.policies.min_free_space and
        self.state and
        get_free_space(self.cache_dir) < self.policies.min_free_space):
      self.remove_lru_file()

    # Ensure maximum cache size.
    if self.policies.max_cache_size and self.state:
      try:
        sizes = [os.stat(self.path(f)).st_size for f in self.state]
      except OSError:
        logging.error(
            'At least one file is missing; %s\n' % '\n'.join(self.state))
        raise

      while sizes and sum(sizes) > self.policies.max_cache_size:
        self.remove_lru_file()
        sizes.pop(0)

    # Ensure maximum number of items in the cache.
    if self.policies.max_items and self.state:
      while len(self.state) > self.policies.max_items:
        self.remove_lru_file()

    self.save()

  def retrieve(self, item):
    """Retrieves a file from the remote and add it to the cache."""
    assert not '/' in item
    try:
      index = self.state.index(item)
      # Was already in cache. Update it's LRU value.
      self.state.pop(index)
      self.state.append(item)
      return False
    except ValueError:
      out = self.path(item)
      start_retrieve = time.time()
      self.remote.fetch_item(Remote.MED, item, out)
      # TODO(maruel): Temporarily fetch the files serially.
      self.remote.get_result()
      if os.path.exists(out):
        self.state.append(item)
        self.files_added.append((out, os.stat(out).st_size))
      else:
        logging.error('File, %s, not placed in cache' % item)
      self.time_retrieving_files += time.time() - start_retrieve
      return True
    finally:
      self.save()

  def path(self, item):
    """Returns the path to one item."""
    return os.path.join(self.cache_dir, item)

  def save(self):
    """Saves the LRU ordering."""
    json.dump(self.state, open(self.state_file, 'wb'), separators=(',',':'))


def run_tha_test(manifest, cache_dir, remote, policies):
  """Downloads the dependencies in the cache, hardlinks them into a temporary
  directory and runs the executable.
  """
  with Cache(cache_dir, Remote(remote), policies) as cache:
    outdir = make_temp_dir('run_tha_test', cache_dir)

    if not 'files' in manifest:
      print >> sys.stderr, 'No file to map'
      return 1
    if not 'command' in manifest:
      print >> sys.stderr, 'No command to map run'
      return 1

    try:
      with Profiler('GetFiles') as _prof:
        for filepath, properties in manifest['files'].iteritems():
          outfile = os.path.join(outdir, filepath)
          outfiledir = os.path.dirname(outfile)
          if not os.path.isdir(outfiledir):
            os.makedirs(outfiledir)
          if 'sha-1' in properties:
            # A normal file.
            infile = properties['sha-1']
            cache.retrieve(infile)
            link_file(outfile, cache.path(infile), HARDLINK)
          elif 'link' in properties:
            # A symlink.
            os.symlink(properties['link'], outfile)
          else:
            raise ConfigError('Unexpected entry: %s' % properties)
          if 'mode' in properties:
            # It's not set on Windows.
            os.chmod(outfile, properties['mode'])

      cwd = os.path.join(outdir, manifest.get('relative_cwd', ''))
      if not os.path.isdir(cwd):
        os.makedirs(cwd)
      if manifest.get('read_only'):
        make_writable(outdir, True)
      cmd = manifest['command']
      # Ensure paths are correctly separated on windows.
      cmd[0] = cmd[0].replace('/', os.path.sep)
      cmd = fix_python_path(cmd)
      logging.info('Running %s, cwd=%s' % (cmd, cwd))
      try:
        with Profiler('RunTest') as _prof:
          return subprocess.call(cmd, cwd=cwd)
      except OSError:
        print >> sys.stderr, 'Failed to run %s; cwd=%s' % (cmd, cwd)
        raise
    finally:
      rmtree(outdir)


def main():
  parser = optparse.OptionParser(
      usage='%prog <options>', description=sys.modules[__name__].__doc__)
  parser.add_option(
      '-v', '--verbose', action='count', default=0, help='Use multiple times')
  parser.add_option('--no-run', action='store_true', help='Skip the run part')

  group = optparse.OptionGroup(parser, 'Data source')
  group.add_option(
      '-m', '--manifest',
      metavar='FILE',
      help='File/url describing what to map or run')
  group.add_option(
      '-H', '--hash',
      help='Hash of the manifest to grab from the hash table')
  parser.add_option_group(group)

  group.add_option(
      '-r', '--remote', metavar='URL', help='Remote where to get the items')
  group = optparse.OptionGroup(parser, 'Cache management')
  group.add_option(
      '--cache',
      default='cache',
      metavar='DIR',
      help='Cache directory, default=%default')
  group.add_option(
      '--max-cache-size',
      type='int',
      metavar='NNN',
      default=20*1024*1024*1024,
      help='Trim if the cache gets larger than this value, default=%default')
  group.add_option(
      '--min-free-space',
      type='int',
      metavar='NNN',
      default=1*1024*1024*1024,
      help='Trim if disk free space becomes lower than this value, '
           'default=%default')
  group.add_option(
      '--max-items',
      type='int',
      metavar='NNN',
      default=100000,
      help='Trim if more than this number of items are in the cache '
           'default=%default')
  parser.add_option_group(group)

  options, args = parser.parse_args()
  level = [logging.ERROR, logging.INFO, logging.DEBUG][min(2, options.verbose)]
  logging.basicConfig(
      level=level,
      format='%(levelname)5s %(module)15s(%(lineno)3d): %(message)s')

  if bool(options.manifest) == bool(options.hash):
    parser.error('One and only one of --manifest or --hash is required.')
  if not options.remote:
    parser.error('--remote is required.')
  if args:
    parser.error('Unsupported args %s' % ' '.join(args))

  if options.hash:
    # First calculate the reference to it.
    options.manifest = '%s/%s' % (options.remote.rstrip('/'), options.hash)
  try:
    manifest = load_manifest(open_remote(options.manifest).read())
  except IOError as e:
    parser.error(
        'Failed to read manifest %s; remote:%s; hash:%s; %s' %
        (options.manifest, options.remote, options.hash, str(e)))

  policies = CachePolicies(
      options.max_cache_size, options.min_free_space, options.max_items)
  try:
    return run_tha_test(
        manifest,
        os.path.abspath(options.cache),
        options.remote,
        policies)
  except (ConfigError, MappingError), e:
    print >> sys.stderr, str(e)
    return 1


if __name__ == '__main__':
  sys.exit(main())
