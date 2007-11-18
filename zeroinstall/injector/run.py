"""
Executes a set of implementations as a program.
"""

# Copyright (C) 2006, Thomas Leonard
# See the README file for details, or visit http://0install.net.

import os, sys
from logging import debug, info

from zeroinstall.injector.model import Interface, SafeException, EnvironmentBinding, DistributionImplementation, ZeroInstallImplementation
from zeroinstall.injector.iface_cache import iface_cache

def do_env_binding(binding, path):
	os.environ[binding.name] = binding.get_value(path,
					os.environ.get(binding.name, None))
	info("%s=%s", binding.name, os.environ[binding.name])

def execute(policy, prog_args, dry_run = False, main = None, wrapper = None):
	"""Execute program. On success, doesn't return. On failure, raises an Exception.
	Returns normally only for a successful dry run.
	
	@precondition: C{policy.ready and policy.get_uncached_implementations() == []}
	"""
	iface = iface_cache.get_interface(policy.root)
		
	for needed_iface in policy.implementation:
		impl = policy.implementation[needed_iface]
		assert impl
		_do_bindings(impl, impl.bindings)
		for dep in impl.requires:
			dep_iface = iface_cache.get_interface(dep.interface)
			dep_impl = policy.get_implementation(dep_iface)
			if isinstance(dep_impl, ZeroInstallImplementation):
				_do_bindings(dep_impl, dep.bindings)
			else:
				debug("Implementation %s is native; no bindings needed", dep_impl)

	root_impl = policy.get_implementation(iface)
	_execute(root_impl, prog_args, dry_run, main, wrapper)

def _do_bindings(impl, bindings):
	for b in bindings:
		if isinstance(b, EnvironmentBinding):
			do_env_binding(b, _get_implementation_path(impl.id))

def _get_implementation_path(id):
	if id.startswith('/'): return id
	return iface_cache.stores.lookup(id)

def execute_selections(selections, prog_args, dry_run = False, main = None, wrapper = None):
	"""Execute program. On success, doesn't return. On failure, raises an Exception.
	Returns normally only for a successful dry run.
	
	@since: 0.27
	@precondition: All implementations are in the cache.
	"""
	sels = selections.selections
	for selection in sels.values():
		_do_bindings(selection, selection.bindings)
		for dep in selection.dependencies:
			dep_impl = sels[dep.interface]
			if not dep_impl.id.startswith('package:'):
				_do_bindings(dep_impl, dep.bindings)
	
	root_impl = sels[selections.interface]
	_execute(root_impl, prog_args, dry_run, main, wrapper)

def test_selections(selections, prog_args, dry_run, main, wrapper = None):
	"""Run the program in a child process, collecting stdout and stderr.
	@return: the output produced by the process
	@since: 0.27
	"""
	args = []
	import tempfile
	output = tempfile.TemporaryFile(prefix = '0launch-test')
	try:
		child = os.fork()
		if child == 0:
			# We are the child
			try:
				try:
					os.dup2(output.fileno(), 1)
					os.dup2(output.fileno(), 2)
					execute_selections(selections, prog_args, dry_run, main)
				except:
					import traceback
					traceback.print_exc()
			finally:
				sys.stdout.flush()
				sys.stderr.flush()
				os._exit(1)

		info("Waiting for test process to finish...")

		pid, status = os.waitpid(child, 0)
		assert pid == child

		output.seek(0)
		results = output.read()
		if status != 0:
			results += "Error from child process: exit code = %d" % status
	finally:
		output.close()
	
	return results

def _execute(root_impl, prog_args, dry_run, main, wrapper):
	assert root_impl is not None

	if root_impl.id.startswith('package:'):
		main = main or root_impl.main
		prog_path = main
	else:
		if main is None:
			main = root_impl.main
		elif main.startswith('/'):
			main = main[1:]
		elif root_impl.main:
			main = os.path.join(os.path.dirname(root_impl.main), main)
		if main:
			prog_path = os.path.join(_get_implementation_path(root_impl.id), main)

	if main is None:
		raise SafeException("Implementation '%s' cannot be executed directly; it is just a library "
				    "to be used by other programs (or missing 'main' attribute)" %
				    root_impl)

	if not os.path.exists(prog_path):
		raise SafeException("File '%s' does not exist.\n"
				"(implementation '%s' + program '%s')" %
				(prog_path, root_impl.id, main))
	if wrapper:
		prog_args = ['-c', wrapper + ' "$@"', '-', prog_path] + list(prog_args)
		prog_path = '/bin/sh'

	if dry_run:
		print "Would execute:", prog_path, ' '.join(prog_args)
	else:
		info("Executing: %s", prog_path)
		sys.stdout.flush()
		sys.stderr.flush()
		try:
			os.execl(prog_path, prog_path, *prog_args)
		except OSError, ex:
			raise SafeException("Failed to run '%s': %s" % (prog_path, str(ex)))