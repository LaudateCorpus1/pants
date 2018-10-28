# coding=utf-8
# Copyright 2018 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from __future__ import absolute_import, division, print_function, unicode_literals

from builtins import object

from pants.backend.native.subsystems.utils.mirrored_target_option_mixin import \
  MirroredTargetOptionMixin


class CompilerOptionSetsMixin(MirroredTargetOptionMixin, object):
  """A mixin for language-scoped that support compiler option sets."""

  @classmethod
  def register_options(cls, register):
    super(CompilerOptionSetsMixin, cls).register_options(register)

    register('--compiler-option-sets', advanced=True, default=(), type=list,
             fingerprint=True,
             help='The default for the "compiler_option_sets" argument '
                  'for targets of this language.')
    register('--fatal-warnings-enabled-args', advanced=True, type=list, fingerprint=True,
             default=list(cls.get_fatal_warnings_enabled_args_default()),
             help='Extra compiler args to use when fatal warnings are enabled.')
    register('--fatal-warnings-disabled-args', advanced=True, type=list, fingerprint=True,
             default=list(cls.get_fatal_warnings_disabled_args_default()),
             help='Extra compiler args to use when fatal warnings are disabled.')
    register('--compiler-option-sets-enabled-args', advanced=True, type=dict, fingerprint=True,
             default=cls.get_compiler_option_sets_enabled_default_value(),
             help='Extra compiler args to use for each enabled option set.')
    register('--compiler-option-sets-disabled-args', advanced=True, type=dict, fingerprint=True,
             default=cls.get_compiler_option_sets_disabled_default_value(),
             help='Extra compiler args to use for each disabled option set.')

  @property
  def compiler_option_sets(self):
    """For every element in this list, enable the corresponding flags on compilation
    of targets.
    :rtype: list
    """
    return self.get_options().compiler_option_sets

  @property
  def compiler_option_sets_enabled_args(self):
    """For every element in this list, enable the corresponding flags on compilation
    of targets.
    :rtype: list
    """
    return self.get_options().compiler_option_sets_enabled_args

  @property
  def compiler_option_sets_disabled_args(self):
    """For every element in this list, enable the corresponding flags on compilation
    of targets.
    :rtype: list
    """
    return self.get_options().compiler_option_sets_disabled_args

  @classmethod
  def get_compiler_option_sets_enabled_default_value(cls):
    """Override to set default for this option."""
    return {}

  @classmethod
  def get_compiler_option_sets_disabled_default_value(cls):
    """Override to set default for this option."""
    return {}

  @classmethod
  def get_fatal_warnings_enabled_args_default(cls):
    """Override to set default for this option."""
    return ()

  @classmethod
  def get_fatal_warnings_disabled_args_default(cls):
    """Override to set default for this option."""
    return ()

  def get_merged_args_for_compiler_option_sets(self, target):
    compiler_option_sets = self.get_target_mirrored_option(
        'compiler_option_sets', target)
    compiler_options = set()

    # Set values for enabled options.
    for option_set_key in compiler_option_sets:
      # Fatal warnings option has special treatment for backwards compatibility.
      if option_set_key == 'fatal_warnings':
        enabled_fatal_warn_args = self.get_options().fatal_warnings_enabled_args
        compiler_options.update(enabled_fatal_warn_args)
      val = self.get_options().compiler_option_sets_enabled_args.get(option_set_key, ())
      compiler_options.update(val)

    # Set values for disabled options.
    for option_set, disabled_args in self.get_options().compiler_option_sets_disabled_args.items():
      # Fatal warnings option has special treatment for backwards compatibility.
      if option_set == 'fatal_warnings':
        disabled_fatal_warn_args = self.get_options().fatal_warnings_disabled_args
        compiler_options.update(disabled_fatal_warn_args)
      if not option_set in compiler_option_sets:
        compiler_options.update(disabled_args)

    return list(compiler_options)
