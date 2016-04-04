# coding=utf-8
# Copyright 2015 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import (absolute_import, division, generators, nested_scopes, print_function,
                        unicode_literals, with_statement)

import os
from collections import defaultdict

from twitter.common.collections import OrderedSet

from pants.backend.jvm.targets.jvm_target import JvmTarget
from pants.backend.jvm.targets.scala_library import ScalaLibrary
from pants.backend.jvm.targets.unpacked_jars import UnpackedJars
from pants.backend.jvm.tasks.classpath_util import ClasspathUtil
from pants.build_graph.build_graph import sort_targets
from pants.build_graph.resources import Resources
from pants.java.distribution.distribution import DistributionLocator
from pants.util.contextutil import open_zip
from pants.util.dirutil import fast_relpath
from pants.util.memo import memoized_method, memoized_property


class JvmDependencyAnalyzer(object):
  """Helper class for tasks which need to analyze source dependencies.

  Primary purpose is to provide a classfile --> target mapping, which subclasses can use in
  determining which targets correspond to the actual source dependencies of any given target.
  """

  def __init__(self, buildroot, runtime_classpath):
    self._buildroot = buildroot
    self._runtime_classpath = runtime_classpath

  @memoized_method
  def files_for_target(self, target):
    """Yields a sequence of abs path of source, class or jar files provided by the target.

    The runtime classpath for a target must already have been finalized for a target in order
    to compute its provided files.
    """
    def gen():
      # Compute src -> target.
      if isinstance(target, JvmTarget):
        for src in target.sources_relative_to_buildroot():
          yield os.path.join(self._buildroot, src)
      # TODO(Tejal Desai): pantsbuild/pants/65: Remove java_sources attribute for ScalaLibrary
      if isinstance(target, ScalaLibrary):
        for java_source in target.java_sources:
          for src in java_source.sources_relative_to_buildroot():
            yield os.path.join(self._buildroot, src)

      # Compute classfile -> target and jar -> target.
      files = ClasspathUtil.classpath_contents((target,), self._runtime_classpath)
      # And jars; for binary deps, zinc doesn't emit precise deps (yet).
      cp_entries = ClasspathUtil.classpath((target,), self._runtime_classpath)
      jars = [cpe for cpe in cp_entries if ClasspathUtil.is_jar(cpe)]
      for coll in [files, jars]:
        for f in coll:
          yield f
    return set(gen())

  def targets_by_file(self, targets):
    """Returns a map from abs path of source, class or jar file to an OrderedSet of targets.

    The value is usually a singleton, because a source or class file belongs to a single target.
    However a single jar may be provided (transitively or intransitively) by multiple JarLibrary
    targets. But if there is a JarLibrary target that depends on a jar directly, then that
    "canonical" target will be the first one in the list of targets.
    """
    targets_by_file = defaultdict(OrderedSet)

    for target in targets:
      for f in self.files_for_target(target):
        targets_by_file[f].add(target)

    return targets_by_file

  def _jar_classfiles(self, jar_file):
    """Returns an iterator over the classfiles inside jar_file."""
    with open_zip(jar_file, 'r') as jar:
      for cls in jar.namelist():
        if cls.endswith(b'.class'):
          yield cls

  @memoized_property
  def bootstrap_jar_classfiles(self):
    """Returns a set of classfiles from the JVM bootstrap jars."""
    bootstrap_jar_classfiles = set()
    for jar_file in self._find_all_bootstrap_jars():
      for cls in self._jar_classfiles(jar_file):
        bootstrap_jar_classfiles.add(cls)
    return bootstrap_jar_classfiles

  def _find_all_bootstrap_jars(self):
    def get_path(key):
      return DistributionLocator.cached().system_properties.get(key, '').split(':')

    def find_jars_in_dirs(dirs):
      ret = []
      for d in dirs:
        if os.path.isdir(d):
          ret.extend(filter(lambda s: s.endswith('.jar'), os.listdir(d)))
      return ret

    # Note: assumes HotSpot, or some JVM that supports sun.boot.class.path.
    # TODO: Support other JVMs? Not clear if there's a standard way to do so.
    # May include loose classes dirs.
    boot_classpath = get_path('sun.boot.class.path')

    # Note that per the specs, overrides and extensions must be in jars.
    # Loose class files will not be found by the JVM.
    override_jars = find_jars_in_dirs(get_path('java.endorsed.dirs'))
    extension_jars = find_jars_in_dirs(get_path('java.ext.dirs'))

    # Note that this order matters: it reflects the classloading order.
    bootstrap_jars = filter(os.path.isfile, override_jars + boot_classpath + extension_jars)
    return bootstrap_jars  # Technically, may include loose class dirs from boot_classpath.

  def compute_transitive_deps_by_target(self, targets):
    """Map from target to all the targets it depends on, transitively."""
    # Sort from least to most dependent.
    sorted_targets = reversed(sort_targets(targets))
    transitive_deps_by_target = defaultdict(set)
    # Iterate in dep order, to accumulate the transitive deps for each target.
    for target in sorted_targets:
      transitive_deps = set()
      for dep in target.dependencies:
        transitive_deps.update(transitive_deps_by_target.get(dep, []))
        transitive_deps.add(dep)

      # Need to handle the case where a java_sources target has dependencies.
      # In particular if it depends back on the original target.
      if hasattr(target, 'java_sources'):
        for java_source_target in target.java_sources:
          for transitive_dep in java_source_target.dependencies:
            transitive_deps_by_target[java_source_target].add(transitive_dep)

      transitive_deps_by_target[target] = transitive_deps
    return transitive_deps_by_target

  def normalize_product_dep(self, classes_by_source, dep):
    """Normalizes the given product dep from the given dep into a set of classfiles.

    Product deps arrive as sources, jars, and classfiles: this method normalizes them to classfiles and jars.
    """
    if dep.endswith(".jar"):
      # NB: We preserve jars "whole" here, because zinc does not support finer granularity.
      return set([dep])
    elif dep.endswith(".class"):
      return set([dep])
    else:
      # Assume a source file and convert to classfiles.
      rel_src = fast_relpath(dep, self._buildroot)
      return set(p for _, paths in classes_by_source[rel_src].rel_paths() for p in paths)

  def compute_unused_deps(self, product_deps_by_src, dep_context, compile_context):
    """Uses `product_deps_by_src` to compute unused deps.

    TODO: Move `compile_context.declared_dependencies` to Target to allow this method
    to take a Target instead of a CompileContext.

    :returns: dict of unused targets to suggested replacements.
    """

    # Flatten the product deps of this target.
    product_deps = set()
    for dep_entries in product_deps_by_src.get(compile_context.target, dict()).values():
      product_deps.update(dep_entries)

    # Determine which of the deps in the declared set of this target were used.
    used = set()
    unused = set()
    for dep in compile_context.declared_dependencies(dep_context,
                                                     compiler_plugins=False,
                                                     exported=False):
      if dep in used or dep in unused:
        continue
      # TODO: What's a better way to accomplish this check? Filtering by `has_sources` would
      # incorrectly skip "empty" `*_library` targets, which could then be used as a loophole.
      if isinstance(dep, (Resources, UnpackedJars)):
        continue
      # If any of the target's jars or classfiles were used, consider it used.
      if product_deps.isdisjoint(self.files_for_target(dep)):
        unused.add(dep)
      else:
        used.add(dep)

    # If there were no unused deps, break.
    if not unused:
      return dict()

    # For any deps that were used, count their derived-from targets used as well.
    # TODO: Refactor to do some of this above once tests are in place.
    for dep in list(used):
      for derived_from in dep.derived_from_chain:
        if derived_from in unused:
          unused.remove(derived_from)
          used.add(derived_from)

    # Prune derived targets that would be in the set twice.
    for dep in list(unused):
      if set(dep.derived_from_chain) & unused:
        unused.remove(dep)

    if not unused:
      return dict()

    # For any deps that were not used, determine whether their transitive deps were used, and
    # recommend those as replacements.
    replacements = dict()
    for dep in unused:
      replacements[dep] = set()
      for t in dep.closure():
        if t in used or t in unused:
          continue
        if not product_deps.isdisjoint(self.files_for_target(t)):
          replacements[dep].add(t.concrete_derived_from)

    return replacements
