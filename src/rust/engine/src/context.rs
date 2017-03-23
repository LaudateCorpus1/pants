// Copyright 2017 Pants project contributors (see CONTRIBUTORS.md).
// Licensed under the Apache License, Version 2.0 (see LICENSE).

use std::path::PathBuf;
use std::sync::{Arc, RwLock, RwLockReadGuard};

use futures_cpupool::{self, CpuPool};

use fs::{PosixFS, Snapshots};
use graph::{EntryId, Graph};
use tasks::Tasks;
use types::Types;


/**
 * The core context shared (via Arc) between the Scheduler and the Context objects of
 * all running Nodes.
 *
 * TODO: Move `nodes.Context` to this module and rename both of these.
 */
pub struct Core {
  pub graph: Graph,
  pub tasks: Tasks,
  pub types: Types,
  pub snapshots: Snapshots,
  pub vfs: PosixFS,
  // TODO: This is a second pool (relative to the VFS pool), upon which all work is
  // submitted. See https://github.com/pantsbuild/pants/issues/4298
  pool: RwLock<CpuPool>,
}

impl Core {
  pub fn new(
    tasks: Tasks,
    types: Types,
    build_root: PathBuf,
    ignore_patterns: Vec<String>,
    work_dir: PathBuf,
  ) -> Core {
    Core {
      graph: Graph::new(),
      tasks: tasks,
      types: types,
      snapshots: Snapshots::new(work_dir)
        .unwrap_or_else(|e| {
          panic!("Could not initialize Snapshot directory: {:?}", e);
        }),
      // FIXME: Errors in initialization should definitely be exposed as python
      // exceptions, rather than as panics.
      vfs:
        PosixFS::new(build_root, ignore_patterns)
        .unwrap_or_else(|e| {
          panic!("Could not initialize VFS: {:?}", e);
        }),
      pool: RwLock::new(Core::create_pool()),
    }
  }

  pub fn pool(&self) -> RwLockReadGuard<CpuPool> {
    self.pool.read().unwrap()
  }

  fn create_pool() -> CpuPool {
    futures_cpupool::Builder::new()
      .name_prefix("engine-")
      .create()
  }

  /**
   * Reinitializes a Core in a new process (basically, recreates its CpuPool).
   */
  pub fn post_fork(&self) {
    // Reinitialize the VFS pool.
    self.vfs.post_fork();
    // And our own.
    let mut pool = self.pool.write().unwrap();
    *pool = Core::create_pool();
  }
}

#[derive(Clone)]
pub struct Context {
  pub entry_id: EntryId,
  pub core: Arc<Core>,
}

impl Context {
  pub fn new(entry_id: EntryId, core: Arc<Core>) -> Context {
    Context {
      entry_id: entry_id,
      core: core,
    }
  }
}

pub trait ContextFactory {
  fn create(&self, entry_id: EntryId) -> Context;
  fn pool(&self) -> RwLockReadGuard<CpuPool>;
}

impl ContextFactory for Context {
  /**
   * Clones this Context for a new EntryId. Because the Core of the context is an Arc, this
   * is a shallow clone.
   */
  fn create(&self, entry_id: EntryId) -> Context {
    Context {
      entry_id: entry_id,
      core: self.core.clone(),
    }
  }

  fn pool(&self) -> RwLockReadGuard<CpuPool> {
    self.core.pool()
  }
}
