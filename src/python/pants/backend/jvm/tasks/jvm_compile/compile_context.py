# coding=utf-8
# Copyright 2015 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

import zipfile
from contextlib import contextmanager

from pants.build_graph.target import Target
from pants.util.contextutil import open_zip


class DependencyContext(object):
  def __init__(self, compiler_plugin_types, target_closure_kwargs):
    self.compiler_plugin_types = compiler_plugin_types
    self.target_closure_kwargs = target_closure_kwargs


class CompileContext(object):
  """A context for the compilation of a target.

  This can be used to differentiate between a partially completed compile in a temporary location
  and a finalized compile in its permanent location.
  """

  def __init__(self, target, analysis_file, portable_analysis_file, classes_dir, jar_file,
               log_file, sources, strict_deps):
    self.target = target
    self.analysis_file = analysis_file
    self.portable_analysis_file = portable_analysis_file
    self.classes_dir = classes_dir
    self.jar_file = jar_file
    self.log_file = log_file
    self.sources = sources
    self.strict_deps = strict_deps

  @contextmanager
  def open_jar(self, mode):
    with open_zip(self.jar_file, mode=mode, compression=zipfile.ZIP_STORED) as jar:
      yield jar

  def dependencies(self, dep_context):
    """Computes the compile time dependencies of this target, in the given DependencyContext."""
    if self.strict_deps:
      return list(self.strict_dependencies(dep_context))
    else:
      return self.all_dependencies(dep_context)

  def strict_dependencies(self, dep_context):
    """Compute the 'strict' compile target dependencies for this target.

    Recursively resolves target aliases, and includes the transitive deps of compiler plugins,
    since compiletime is actually runtime for them.
    """
    def resolve(t):
      for declared in t.dependencies:
        if type(declared) == Target:
          for r in resolve(declared):
            yield r
        elif isinstance(declared, dep_context.compiler_plugin_types):
          for r in declared.closure(bfs=True, **dep_context.target_closure_kwargs):
            yield r
        else:
          yield declared

    yield self.target
    for dep in resolve(self.target):
      yield dep

  def all_dependencies(self, dep_context):
    """All transitive dependencies of the context's target."""
    return self.target.closure(bfs=True, **dep_context.target_closure_kwargs)

  @property
  def _id(self):
    return (self.target, self.analysis_file, self.classes_dir)

  def __eq__(self, other):
    return self._id == other._id

  def __ne__(self, other):
    return self._id != other._id

  def __hash__(self):
    return hash(self._id)
