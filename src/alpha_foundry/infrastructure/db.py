"""SQLite persistence primitives for Alpha Foundry.

The store owns SQLite setup and transaction boundaries only. Application services must
persist intent before calling providers, engines, or artifact storage, and must never
hold one of these transactions while performing that external work.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from math import isfinite
from pathlib import Path
from threading import RLock, local
from typing import Final, cast

SCHEMA: Final[str] = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    stage TEXT NOT NULL,
    command_fingerprint TEXT NOT NULL,
    progress_json TEXT NOT NULL,
    result_ref TEXT,
    error_json TEXT,
    execution_owner TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL CHECK (row_version >= 1),
    UNIQUE (kind, command_fingerprint),
    CHECK (
        (status = 'QUEUED' AND started_at IS NULL AND finished_at IS NULL)
        OR (status = 'RUNNING' AND started_at IS NOT NULL AND finished_at IS NULL)
        OR (status IN ('SUCCEEDED', 'FAILED', 'CANCELLED') AND finished_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS jobs_status_created_idx ON jobs (status, created_at, job_id);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    operation TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    job_id TEXT,
    final_response_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (operation, idempotency_key),
    FOREIGN KEY (job_id) REFERENCES jobs (job_id)
);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    cas_uri TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL UNIQUE,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    media_type TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    code_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_links (
    artifact_link_id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    owner_type TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    role TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (owner_type, owner_id, role),
    FOREIGN KEY (artifact_id) REFERENCES artifacts (artifact_id)
);
CREATE INDEX IF NOT EXISTS artifact_links_artifact_idx ON artifact_links (artifact_id);

CREATE TABLE IF NOT EXISTS immutable_resources (
    resource_id TEXT PRIMARY KEY,
    resource_kind TEXT NOT NULL,
    resource_key TEXT NOT NULL,
    version TEXT NOT NULL,
    semantic_json TEXT NOT NULL,
    canonical_bytes_hash TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    UNIQUE (resource_kind, resource_key, version)
);

CREATE TABLE IF NOT EXISTS knowledge_claims (
    claim_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    claim_json TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    PRIMARY KEY (claim_id, version)
);

CREATE TABLE IF NOT EXISTS knowledge_packs (
    knowledge_pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL UNIQUE,
    pack_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    PRIMARY KEY (knowledge_pack_id, version)
);

CREATE TABLE IF NOT EXISTS knowledge_pack_claims (
    knowledge_pack_id TEXT NOT NULL,
    knowledge_pack_version TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    claim_version TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    PRIMARY KEY (knowledge_pack_id, knowledge_pack_version, claim_id, claim_version),
    UNIQUE (knowledge_pack_id, knowledge_pack_version, ordinal),
    FOREIGN KEY (knowledge_pack_id, knowledge_pack_version)
        REFERENCES knowledge_packs (knowledge_pack_id, version),
    FOREIGN KEY (claim_id, claim_version) REFERENCES knowledge_claims (claim_id, version)
);

CREATE TABLE IF NOT EXISTS generation_requests (
    generation_request_id TEXT PRIMARY KEY,
    request_hash TEXT NOT NULL UNIQUE,
    capability_snapshot_hash TEXT NOT NULL,
    knowledge_pack_hash TEXT NOT NULL,
    claim_pins_json TEXT NOT NULL,
    provider_chain_json TEXT NOT NULL,
    request_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('AVAILABLE', 'RUNNING', 'ACCEPTED', 'FAILED')),
    owner_token TEXT,
    owner_job_id TEXT,
    lease_epoch INTEGER NOT NULL CHECK (lease_epoch >= 0),
    lease_expires_at TEXT,
    next_ordinal INTEGER NOT NULL CHECK (next_ordinal >= 0),
    accepted_artifact_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    row_version INTEGER NOT NULL CHECK (row_version >= 1),
    FOREIGN KEY (owner_job_id) REFERENCES jobs (job_id),
    FOREIGN KEY (accepted_artifact_id) REFERENCES artifacts (artifact_id),
    CHECK (
        (state = 'AVAILABLE' AND owner_token IS NULL AND owner_job_id IS NULL
            AND lease_expires_at IS NULL AND accepted_artifact_id IS NULL)
        OR (state = 'RUNNING' AND owner_token IS NOT NULL AND owner_job_id IS NOT NULL
            AND lease_epoch >= 1 AND lease_expires_at IS NOT NULL AND accepted_artifact_id IS NULL)
        OR (state = 'ACCEPTED' AND owner_token IS NULL AND owner_job_id IS NULL
            AND lease_expires_at IS NULL AND accepted_artifact_id IS NOT NULL)
        OR (state = 'FAILED' AND owner_token IS NULL AND owner_job_id IS NULL
            AND lease_expires_at IS NULL AND accepted_artifact_id IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS generation_requests_state_idx ON generation_requests (state, created_at);

CREATE TABLE IF NOT EXISTS generation_owner_events (
    event_id TEXT PRIMARY KEY,
    generation_request_id TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK (event_kind IN ('ACQUIRED', 'RELEASED_INTERRUPTED', 'TRANSFERRED', 'COMPLETED')),
    old_owner_job_id TEXT,
    old_owner_token TEXT,
    old_owner_epoch INTEGER,
    new_owner_job_id TEXT,
    new_owner_token TEXT,
    new_owner_epoch INTEGER,
    related_attempt_id TEXT,
    prior_event_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (generation_request_id) REFERENCES generation_requests (generation_request_id),
    FOREIGN KEY (related_attempt_id) REFERENCES generation_attempts (attempt_id),
    FOREIGN KEY (prior_event_id) REFERENCES generation_owner_events (event_id)
);
CREATE INDEX IF NOT EXISTS generation_owner_events_request_idx
    ON generation_owner_events (generation_request_id, created_at, event_id);

CREATE TABLE IF NOT EXISTS generation_attempts (
    attempt_id TEXT PRIMARY KEY,
    generation_request_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    provider_config_hash TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_hash TEXT,
    state TEXT NOT NULL CHECK (state IN ('STARTED', 'SUCCEEDED_VALID', 'FAILED', 'INVALID', 'INTERRUPTED')),
    error_code TEXT,
    accepted_artifact_id TEXT,
    started_at TEXT NOT NULL,
    terminal_at TEXT,
    owner_epoch INTEGER NOT NULL CHECK (owner_epoch >= 1),
    UNIQUE (generation_request_id, ordinal),
    FOREIGN KEY (generation_request_id) REFERENCES generation_requests (generation_request_id),
    FOREIGN KEY (accepted_artifact_id) REFERENCES artifacts (artifact_id),
    CHECK (
        (state = 'STARTED' AND terminal_at IS NULL AND response_hash IS NULL
            AND error_code IS NULL AND accepted_artifact_id IS NULL)
        OR (state = 'SUCCEEDED_VALID' AND terminal_at IS NOT NULL AND response_hash IS NOT NULL
            AND error_code IS NULL AND accepted_artifact_id IS NOT NULL)
        OR (state IN ('FAILED', 'INVALID', 'INTERRUPTED') AND terminal_at IS NOT NULL
            AND accepted_artifact_id IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS generation_attempts_request_idx
    ON generation_attempts (generation_request_id, ordinal);

CREATE TABLE IF NOT EXISTS search_runs (
    search_run_id TEXT PRIMARY KEY,
    lineage_id TEXT NOT NULL,
    search_spec_hash TEXT NOT NULL,
    universe_hash TEXT NOT NULL,
    profile_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    stop_reason TEXT CHECK (stop_reason IS NULL OR stop_reason IN
        ('PLATEAU', 'UNIVERSE_EXHAUSTED', 'FAILED_INVARIANT', 'INTERRUPTED')),
    best_candidate_hash TEXT,
    plateau_counter INTEGER NOT NULL CHECK (plateau_counter >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    row_version INTEGER NOT NULL CHECK (row_version >= 1),
    UNIQUE (lineage_id)
);
CREATE INDEX IF NOT EXISTS search_runs_state_idx ON search_runs (state, created_at);

CREATE TABLE IF NOT EXISTS search_run_events (
    event_id TEXT PRIMARY KEY,
    search_run_id TEXT NOT NULL,
    event_kind TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (search_run_id) REFERENCES search_runs (search_run_id)
);
CREATE INDEX IF NOT EXISTS search_run_events_run_idx ON search_run_events (search_run_id, created_at, event_id);

CREATE TABLE IF NOT EXISTS search_generation_parent_pools (
    search_run_id TEXT NOT NULL,
    generation_index INTEGER NOT NULL CHECK (generation_index >= 1),
    ordered_candidate_hashes_json TEXT NOT NULL,
    parent_pool_digest TEXT NOT NULL,
    profile_hash TEXT NOT NULL,
    search_spec_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (search_run_id, generation_index),
    UNIQUE (search_run_id, generation_index, parent_pool_digest),
    FOREIGN KEY (search_run_id) REFERENCES search_runs (search_run_id)
);

CREATE TABLE IF NOT EXISTS search_run_proposals (
    search_run_id TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    generation_index INTEGER NOT NULL CHECK (generation_index >= 0),
    slot_index INTEGER NOT NULL CHECK (slot_index >= 0),
    ledger_position INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (search_run_id, candidate_hash),
    UNIQUE (search_run_id, generation_index, slot_index),
    FOREIGN KEY (search_run_id) REFERENCES search_runs (search_run_id)
);

CREATE TABLE IF NOT EXISTS candidate_trials (
    candidate_trial_id TEXT PRIMARY KEY,
    search_run_id TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    parent_hashes_json TEXT NOT NULL,
    operator_id TEXT NOT NULL,
    parameter_indices_json TEXT NOT NULL,
    generation_index INTEGER NOT NULL CHECK (generation_index >= 0),
    slot_index INTEGER NOT NULL CHECK (slot_index >= 0),
    ledger_position INTEGER NOT NULL CHECK (ledger_position >= 0),
    state TEXT NOT NULL CHECK (state IN
        ('STARTED', 'EVALUATED', 'REJECTED_PREFLIGHT', 'REJECTED_HARD_GATE', 'ENGINE_FAILED', 'INTERRUPTED')),
    started_at TEXT NOT NULL,
    terminal_at TEXT,
    score_vector_json TEXT,
    hard_gate_passed INTEGER CHECK (hard_gate_passed IN (0, 1)),
    row_version INTEGER NOT NULL CHECK (row_version >= 1),
    UNIQUE (search_run_id, ledger_position),
    UNIQUE (search_run_id, generation_index, slot_index),
    UNIQUE (search_run_id, candidate_hash),
    FOREIGN KEY (search_run_id) REFERENCES search_runs (search_run_id),
    CHECK (
        (state = 'STARTED' AND terminal_at IS NULL AND score_vector_json IS NULL
            AND hard_gate_passed IS NULL)
        OR (state != 'STARTED' AND terminal_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS candidate_trials_run_state_idx ON candidate_trials (search_run_id, state);

CREATE TABLE IF NOT EXISTS search_run_visited (
    search_run_id TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    candidate_trial_id TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (search_run_id, candidate_hash),
    FOREIGN KEY (search_run_id) REFERENCES search_runs (search_run_id),
    FOREIGN KEY (candidate_trial_id) REFERENCES candidate_trials (candidate_trial_id)
);

CREATE TABLE IF NOT EXISTS candidate_trial_events (
    trial_event_id TEXT PRIMARY KEY,
    candidate_trial_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 0),
    event_kind TEXT NOT NULL CHECK (event_kind IN
        ('TRIAL_STARTED', 'INVALID', 'OUTSIDE_UNIVERSE', 'DUPLICATE', 'DIRECT_FALLBACK', 'SLOT_EXHAUSTED',
         'EVALUATED', 'REJECTED_PREFLIGHT', 'REJECTED_HARD_GATE', 'ENGINE_FAILED', 'INTERRUPTED')),
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (candidate_trial_id, sequence),
    FOREIGN KEY (candidate_trial_id) REFERENCES candidate_trials (candidate_trial_id)
);

CREATE TABLE IF NOT EXISTS candidate_trial_folds (
    candidate_trial_id TEXT NOT NULL,
    fold_id TEXT NOT NULL,
    metric_vector_json TEXT NOT NULL,
    hard_gate_passed INTEGER NOT NULL CHECK (hard_gate_passed IN (0, 1)),
    complete_finite INTEGER NOT NULL CHECK (complete_finite IN (0, 1)),
    evidence_hash TEXT NOT NULL,
    PRIMARY KEY (candidate_trial_id, fold_id),
    FOREIGN KEY (candidate_trial_id) REFERENCES candidate_trials (candidate_trial_id)
);

CREATE TABLE IF NOT EXISTS pbo_certificates (
    pbo_certificate_id TEXT PRIMARY KEY,
    search_run_id TEXT NOT NULL UNIQUE,
    profile_hash TEXT NOT NULL,
    started_trial_ids_json TEXT NOT NULL,
    terminal_trial_ids_json TEXT NOT NULL,
    eligible_trial_ids_json TEXT NOT NULL,
    matrix_trial_ids_json TEXT NOT NULL,
    expected_fold_ids_json TEXT NOT NULL,
    observed_fold_ids_json TEXT NOT NULL,
    started_count INTEGER NOT NULL CHECK (started_count >= 0),
    terminal_count INTEGER NOT NULL CHECK (terminal_count >= 0),
    eligible_count INTEGER NOT NULL CHECK (eligible_count >= 0),
    matrix_count INTEGER NOT NULL CHECK (matrix_count >= 0),
    artifact_id TEXT,
    decision TEXT NOT NULL CHECK (decision IN ('PASS', 'FAIL')),
    reason_code TEXT,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    FOREIGN KEY (search_run_id) REFERENCES search_runs (search_run_id),
    FOREIGN KEY (artifact_id) REFERENCES artifacts (artifact_id),
    CHECK ((decision = 'PASS' AND reason_code IS NULL) OR decision = 'FAIL')
);

CREATE TABLE IF NOT EXISTS lineages (
    lineage_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    closed_at TEXT,
    close_reason TEXT,
    root_refs_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    CHECK (
        (status = 'OPEN' AND closed_at IS NULL AND close_reason IS NULL)
        OR (status = 'CLOSED' AND closed_at IS NOT NULL AND close_reason IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS validations (
    validation_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    profile_hash TEXT NOT NULL,
    pre_validation_decision TEXT NOT NULL CHECK (pre_validation_decision IN ('PASS', 'FAIL')),
    gate_summary_json TEXT NOT NULL,
    fold_summary_json TEXT NOT NULL,
    pbo_certificate_id TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    FOREIGN KEY (lineage_id) REFERENCES lineages (lineage_id),
    FOREIGN KEY (pbo_certificate_id) REFERENCES pbo_certificates (pbo_certificate_id)
);

CREATE TABLE IF NOT EXISTS holdout_slots (
    lineage_id TEXT PRIMARY KEY,
    profile_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('UNUSED', 'CONSUMED')),
    consumed_at TEXT,
    consuming_validation_id TEXT,
    selector_hash TEXT,
    row_version INTEGER NOT NULL CHECK (row_version >= 1),
    FOREIGN KEY (lineage_id) REFERENCES lineages (lineage_id),
    FOREIGN KEY (consuming_validation_id) REFERENCES validations (validation_id),
    CHECK (
        (state = 'UNUSED' AND consumed_at IS NULL AND consuming_validation_id IS NULL AND selector_hash IS NULL)
        OR (state = 'CONSUMED' AND consumed_at IS NOT NULL AND consuming_validation_id IS NOT NULL
            AND selector_hash IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS holdout_events (
    holdout_event_id TEXT PRIMARY KEY,
    lineage_id TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK (event_kind = 'CONSUMED'),
    validation_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (lineage_id) REFERENCES lineages (lineage_id),
    FOREIGN KEY (validation_id) REFERENCES validations (validation_id),
    UNIQUE (lineage_id, event_kind)
);

CREATE TABLE IF NOT EXISTS publications (
    publication_id TEXT PRIMARY KEY,
    validation_id TEXT NOT NULL UNIQUE,
    strategy_id TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    profile_hash TEXT,
    registry_entry_id TEXT,
    state TEXT NOT NULL CHECK (state IN ('AVAILABLE', 'PREPARING', 'PUBLISHED', 'FAILED_RETRYABLE')),
    owner_token TEXT,
    owner_job_id TEXT,
    owner_epoch INTEGER NOT NULL CHECK (owner_epoch >= 0),
    owner_expires_at TEXT,
    attempt_count INTEGER NOT NULL CHECK (attempt_count >= 0),
    prepared_orphan_hashes_json TEXT,
    failure_kind TEXT CHECK (failure_kind IS NULL OR failure_kind IN
        ('ARTIFACT_IO', 'REPORT_RENDER', 'STORAGE_COMMIT', 'OWNER_INTERRUPTED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    published_at TEXT,
    row_version INTEGER NOT NULL CHECK (row_version >= 1),
    FOREIGN KEY (validation_id) REFERENCES validations (validation_id),
    FOREIGN KEY (lineage_id) REFERENCES lineages (lineage_id),
    FOREIGN KEY (owner_job_id) REFERENCES jobs (job_id),
    CHECK (
        (state = 'PREPARING' AND owner_token IS NOT NULL AND owner_job_id IS NOT NULL
            AND owner_epoch >= 1 AND owner_expires_at IS NOT NULL)
        OR (state IN ('AVAILABLE', 'FAILED_RETRYABLE', 'PUBLISHED') AND owner_token IS NULL
            AND owner_job_id IS NULL AND owner_expires_at IS NULL)
    ),
    CHECK ((state = 'PUBLISHED' AND published_at IS NOT NULL AND failure_kind IS NULL)
        OR (state != 'PUBLISHED' AND published_at IS NULL))
);
CREATE INDEX IF NOT EXISTS publications_state_idx ON publications (state, created_at);

CREATE TABLE IF NOT EXISTS publication_owner_events (
    event_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK (event_kind IN ('ACQUIRED', 'RELEASED_STALE', 'FAILED_ATTEMPT', 'PUBLISHED')),
    owner_job_id TEXT,
    owner_epoch INTEGER,
    orphan_hashes_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (publication_id) REFERENCES publications (publication_id)
);
CREATE INDEX IF NOT EXISTS publication_owner_events_publication_idx
    ON publication_owner_events (publication_id, created_at, event_id);

CREATE TABLE IF NOT EXISTS publication_rejections (
    publication_rejection_id TEXT PRIMARY KEY,
    validation_id TEXT,
    input_hash TEXT NOT NULL,
    reason_code TEXT NOT NULL CHECK (reason_code IN
        ('VALIDATION_NOT_PASS', 'LINEAGE_NOT_CLOSED', 'DISCLOSURE_INVALID', 'INPUT_HASH_MISMATCH')),
    created_at TEXT NOT NULL,
    UNIQUE (validation_id, input_hash)
);

CREATE TABLE IF NOT EXISTS registry_entries (
    registry_entry_id TEXT PRIMARY KEY,
    publication_id TEXT NOT NULL UNIQUE,
    validation_id TEXT NOT NULL UNIQUE,
    strategy_id TEXT NOT NULL,
    candidate_hash TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (publication_id) REFERENCES publications (publication_id),
    FOREIGN KEY (validation_id) REFERENCES validations (validation_id)
);

CREATE TABLE IF NOT EXISTS rejection_registry (
    rejection_id TEXT PRIMARY KEY,
    lineage_id TEXT NOT NULL,
    candidate_hash TEXT,
    strategy_id TEXT,
    validation_id TEXT,
    terminal_stage TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    redacted_summary TEXT NOT NULL,
    artifact_id TEXT,
    evidence_hash TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (lineage_id) REFERENCES lineages (lineage_id),
    FOREIGN KEY (validation_id) REFERENCES validations (validation_id),
    FOREIGN KEY (artifact_id) REFERENCES artifacts (artifact_id)
);
CREATE INDEX IF NOT EXISTS rejection_registry_lineage_idx ON rejection_registry (lineage_id, created_at);

CREATE VIEW IF NOT EXISTS published_strategy_view AS
SELECT
    registry_entries.registry_entry_id,
    registry_entries.publication_id,
    registry_entries.validation_id,
    registry_entries.strategy_id,
    registry_entries.candidate_hash,
    registry_entries.lineage_id,
    publications.published_at
FROM registry_entries
JOIN publications ON publications.publication_id = registry_entries.publication_id
WHERE publications.state = 'PUBLISHED';

"""
FREEZE_TRIGGER_NAMES: Final[tuple[str, ...]] = (
    "generation_requests_accepted_immutable",
    "holdout_slots_consumed_immutable",
    "holdout_slots_consumed_not_deleted",
    "lineages_closed_immutable",
    "lineages_closed_not_deleted",
    "lineages_closed_not_replaced",
    "publications_published_immutable",
    "publications_published_not_deleted",
    "generation_requests_accepted_not_replaced",
    "holdout_slots_consumed_not_replaced",
    "publications_published_not_replaced",
    "publications_registry_identity_immutable",
    "publications_profile_identity_immutable",
    "publications_published_profile_bound_on_insert",
    "publications_published_profile_bound_on_update",
    "validations_published_immutable",
    "validations_published_not_deleted",
    "validations_published_not_replaced",
    "pbo_certificates_published_immutable",
    "pbo_certificates_published_not_deleted",
    "pbo_certificates_published_not_replaced",
    "registry_entries_published_immutable",
    "registry_entries_published_not_deleted",
    "registry_entries_published_not_inserted",
    "artifact_links_published_immutable",
    "artifact_links_published_not_deleted",
    "artifact_links_published_not_inserted",
    "artifacts_published_immutable",
    "artifacts_published_not_deleted",
)

FREEZE_TRIGGERS: Final[str] = """
CREATE TRIGGER IF NOT EXISTS generation_requests_accepted_immutable
BEFORE UPDATE ON generation_requests
WHEN OLD.state = 'ACCEPTED'
BEGIN
    SELECT RAISE(ABORT, 'accepted generation request is immutable');
END;
CREATE TRIGGER IF NOT EXISTS holdout_slots_consumed_immutable
BEFORE UPDATE ON holdout_slots
WHEN OLD.state = 'CONSUMED'
BEGIN
    SELECT RAISE(ABORT, 'consumed holdout slot is immutable');
END;
CREATE TRIGGER IF NOT EXISTS holdout_slots_consumed_not_deleted
BEFORE DELETE ON holdout_slots
WHEN OLD.state = 'CONSUMED'
BEGIN
    SELECT RAISE(ABORT, 'consumed holdout slot cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS lineages_closed_immutable
BEFORE UPDATE ON lineages
WHEN OLD.status = 'CLOSED'
BEGIN
    SELECT RAISE(ABORT, 'closed lineage is immutable');
END;
CREATE TRIGGER IF NOT EXISTS lineages_closed_not_deleted
BEFORE DELETE ON lineages
WHEN OLD.status = 'CLOSED'
BEGIN
    SELECT RAISE(ABORT, 'closed lineage cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS lineages_closed_not_replaced
BEFORE INSERT ON lineages
WHEN EXISTS (
    SELECT 1 FROM lineages
    WHERE status = 'CLOSED' AND lineage_id = NEW.lineage_id
)
BEGIN
    SELECT RAISE(ABORT, 'closed lineage cannot be replaced');
END;
CREATE TRIGGER IF NOT EXISTS publications_published_immutable
BEFORE UPDATE ON publications
WHEN OLD.state = 'PUBLISHED'
BEGIN
    SELECT RAISE(ABORT, 'published publication is immutable');
END;
CREATE TRIGGER IF NOT EXISTS publications_published_not_deleted
BEFORE DELETE ON publications
WHEN OLD.state = 'PUBLISHED'
BEGIN
    SELECT RAISE(ABORT, 'published publication cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS generation_requests_accepted_not_replaced
BEFORE INSERT ON generation_requests
WHEN EXISTS (
    SELECT 1 FROM generation_requests
    WHERE state = 'ACCEPTED'
      AND (generation_request_id = NEW.generation_request_id OR request_hash = NEW.request_hash)
)
BEGIN
    SELECT RAISE(ABORT, 'accepted generation request cannot be replaced');
END;
CREATE TRIGGER IF NOT EXISTS holdout_slots_consumed_not_replaced
BEFORE INSERT ON holdout_slots
WHEN EXISTS (
    SELECT 1 FROM holdout_slots
    WHERE state = 'CONSUMED' AND lineage_id = NEW.lineage_id
)
BEGIN
    SELECT RAISE(ABORT, 'consumed holdout slot cannot be replaced');
END;
CREATE TRIGGER IF NOT EXISTS publications_published_not_replaced
BEFORE INSERT ON publications
WHEN EXISTS (
    SELECT 1 FROM publications
    WHERE state = 'PUBLISHED'
      AND (publication_id = NEW.publication_id OR validation_id = NEW.validation_id)
)
BEGIN
    SELECT RAISE(ABORT, 'published publication cannot be replaced');
END;
CREATE TRIGGER IF NOT EXISTS publications_registry_identity_immutable
BEFORE UPDATE OF registry_entry_id ON publications
WHEN OLD.registry_entry_id IS NOT NULL AND NEW.registry_entry_id IS NOT OLD.registry_entry_id
BEGIN
    SELECT RAISE(ABORT, 'publication registry identity is immutable');
END;
CREATE TRIGGER IF NOT EXISTS publications_profile_identity_immutable
BEFORE UPDATE OF profile_hash ON publications
WHEN OLD.profile_hash IS NOT NULL AND NEW.profile_hash IS NOT OLD.profile_hash
BEGIN
    SELECT RAISE(ABORT, 'publication profile identity is immutable');
END;
CREATE TRIGGER IF NOT EXISTS publications_published_profile_bound_on_insert
BEFORE INSERT ON publications
WHEN NEW.state = 'PUBLISHED' AND NOT EXISTS (
    SELECT 1
    FROM validations
    JOIN pbo_certificates ON pbo_certificates.pbo_certificate_id = validations.pbo_certificate_id
    JOIN holdout_slots ON holdout_slots.lineage_id = validations.lineage_id
    WHERE validations.validation_id = NEW.validation_id
      AND validations.profile_hash = NEW.profile_hash
      AND pbo_certificates.profile_hash = NEW.profile_hash
      AND holdout_slots.profile_hash = NEW.profile_hash
      AND holdout_slots.consuming_validation_id = NEW.validation_id
)
BEGIN
    SELECT RAISE(ABORT, 'published publication requires matching profile provenance');
END;
CREATE TRIGGER IF NOT EXISTS publications_published_profile_bound_on_update
BEFORE UPDATE OF state ON publications
WHEN NEW.state = 'PUBLISHED' AND NOT EXISTS (
    SELECT 1
    FROM validations
    JOIN pbo_certificates ON pbo_certificates.pbo_certificate_id = validations.pbo_certificate_id
    JOIN holdout_slots ON holdout_slots.lineage_id = validations.lineage_id
    WHERE validations.validation_id = NEW.validation_id
      AND validations.profile_hash = NEW.profile_hash
      AND pbo_certificates.profile_hash = NEW.profile_hash
      AND holdout_slots.profile_hash = NEW.profile_hash
      AND holdout_slots.consuming_validation_id = NEW.validation_id
)
BEGIN
    SELECT RAISE(ABORT, 'published publication requires matching profile provenance');
END;
CREATE TRIGGER IF NOT EXISTS validations_published_immutable
BEFORE UPDATE ON validations
WHEN EXISTS (
    SELECT 1 FROM publications
    WHERE publications.validation_id = OLD.validation_id
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published validation provenance is immutable');
END;
CREATE TRIGGER IF NOT EXISTS validations_published_not_deleted
BEFORE DELETE ON validations
WHEN EXISTS (
    SELECT 1 FROM publications
    WHERE publications.validation_id = OLD.validation_id
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published validation provenance cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS validations_published_not_replaced
BEFORE INSERT ON validations
WHEN EXISTS (
    SELECT 1
    FROM validations
    JOIN publications ON publications.validation_id = validations.validation_id
    WHERE publications.state = 'PUBLISHED'
      AND (
          validations.validation_id = NEW.validation_id
          OR validations.content_hash = NEW.content_hash
      )
)
BEGIN
    SELECT RAISE(ABORT, 'published validation provenance cannot be replaced');
END;
CREATE TRIGGER IF NOT EXISTS pbo_certificates_published_immutable
BEFORE UPDATE ON pbo_certificates
WHEN EXISTS (
    SELECT 1
    FROM validations
    JOIN publications ON publications.validation_id = validations.validation_id
    WHERE validations.pbo_certificate_id = OLD.pbo_certificate_id
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published PBO provenance is immutable');
END;
CREATE TRIGGER IF NOT EXISTS pbo_certificates_published_not_deleted
BEFORE DELETE ON pbo_certificates
WHEN EXISTS (
    SELECT 1
    FROM validations
    JOIN publications ON publications.validation_id = validations.validation_id
    WHERE validations.pbo_certificate_id = OLD.pbo_certificate_id
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published PBO provenance cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS pbo_certificates_published_not_replaced
BEFORE INSERT ON pbo_certificates
WHEN EXISTS (
    SELECT 1
    FROM pbo_certificates
    JOIN validations ON validations.pbo_certificate_id = pbo_certificates.pbo_certificate_id
    JOIN publications ON publications.validation_id = validations.validation_id
    WHERE publications.state = 'PUBLISHED'
      AND (
          pbo_certificates.pbo_certificate_id = NEW.pbo_certificate_id
          OR pbo_certificates.search_run_id = NEW.search_run_id
      )
)
BEGIN
    SELECT RAISE(ABORT, 'published PBO provenance cannot be replaced');
END;
CREATE TRIGGER IF NOT EXISTS registry_entries_published_immutable
BEFORE UPDATE ON registry_entries
WHEN EXISTS (
    SELECT 1 FROM publications
    WHERE publications.publication_id = OLD.publication_id
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published registry entry is immutable');
END;
CREATE TRIGGER IF NOT EXISTS registry_entries_published_not_deleted
BEFORE DELETE ON registry_entries
WHEN EXISTS (
    SELECT 1 FROM publications
    WHERE publications.publication_id = OLD.publication_id
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published registry entry cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS registry_entries_published_not_inserted
BEFORE INSERT ON registry_entries
WHEN EXISTS (
    SELECT 1 FROM publications
    WHERE publications.publication_id = NEW.publication_id
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published registry entry cannot be added');
END;
CREATE TRIGGER IF NOT EXISTS artifact_links_published_immutable
BEFORE UPDATE ON artifact_links
WHEN EXISTS (
    SELECT 1
    FROM artifact_links AS links
    JOIN publications ON publications.publication_id = links.owner_id
    WHERE links.artifact_id = OLD.artifact_id
      AND links.owner_type = 'PUBLICATION'
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published artifact link is immutable');
END;
CREATE TRIGGER IF NOT EXISTS artifact_links_published_not_deleted
BEFORE DELETE ON artifact_links
WHEN EXISTS (
    SELECT 1
    FROM artifact_links AS links
    JOIN publications ON publications.publication_id = links.owner_id
    WHERE links.artifact_id = OLD.artifact_id
      AND links.owner_type = 'PUBLICATION'
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published artifact link cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS artifact_links_published_not_inserted
BEFORE INSERT ON artifact_links
WHEN EXISTS (
    SELECT 1
    FROM publications
    WHERE publications.publication_id = NEW.owner_id
      AND publications.state = 'PUBLISHED'
      AND NEW.owner_type = 'PUBLICATION'
)
OR EXISTS (
    SELECT 1
    FROM artifact_links AS links
    JOIN publications ON publications.publication_id = links.owner_id
    WHERE links.artifact_id = NEW.artifact_id
      AND links.owner_type = 'PUBLICATION'
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published artifact graph cannot be extended');
END;
CREATE TRIGGER IF NOT EXISTS artifacts_published_immutable
BEFORE UPDATE ON artifacts
WHEN EXISTS (
    SELECT 1
    FROM artifact_links
    JOIN publications ON publications.publication_id = artifact_links.owner_id
    WHERE artifact_links.artifact_id = OLD.artifact_id
      AND artifact_links.owner_type = 'PUBLICATION'
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published artifact is immutable');
END;
CREATE TRIGGER IF NOT EXISTS artifacts_published_not_deleted
BEFORE DELETE ON artifacts
WHEN EXISTS (
    SELECT 1
    FROM artifact_links
    JOIN publications ON publications.publication_id = artifact_links.owner_id
    WHERE artifact_links.artifact_id = OLD.artifact_id
      AND artifact_links.owner_type = 'PUBLICATION'
      AND publications.state = 'PUBLISHED'
)
BEGIN
    SELECT RAISE(ABORT, 'published artifact cannot be deleted');
END;
"""


class StorageError(RuntimeError):
    """Raised when the local metadata store cannot preserve its invariants."""


class ConcurrentUpdateError(StorageError):
    """Raised when a compare-and-swap update did not affect exactly one row."""


class SQLiteStore:
    """A single-process SQLite WAL store with explicit, short transactions.

    A store owns one connection protected by a re-entrant lock. This intentionally
    supports ``:memory:`` in unit-level consumers while retaining SQLite's single
    writer semantics. The lock is held only for the lexical transaction body.
    """

    def __init__(self, database_path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        path = Path(database_path)
        if str(database_path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = path
        self._timeout_milliseconds = max(1, round(timeout_seconds * 1000))
        self._lock = RLock()
        self._state = local()
        self._quarantined = False
        self._connection = sqlite3.connect(
            str(database_path),
            isolation_level=None,
            check_same_thread=False,
            timeout=timeout_seconds,
        )
        self._connection.row_factory = sqlite3.Row
        self._configure()
        self.initialize()

    def _configure(self) -> None:
        with self._lock:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute(f"PRAGMA busy_timeout = {self._timeout_milliseconds}")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")

    def initialize(self) -> None:
        """Create, migrate, then install the approved immutable metadata schema."""
        with self._lock:
            self._connection.executescript(SCHEMA)
            with self.transaction(immediate=True) as connection:
                for trigger_name in FREEZE_TRIGGER_NAMES:
                    connection.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")
                columns = {
                    str(row["name"])
                    for row in connection.execute("PRAGMA table_info(publications)").fetchall()
                }
                if "registry_entry_id" not in columns:
                    connection.execute("ALTER TABLE publications ADD COLUMN registry_entry_id TEXT")
                connection.execute(
                    """
                    UPDATE publications
                    SET registry_entry_id = (
                        SELECT registry_entries.registry_entry_id
                        FROM registry_entries
                        WHERE registry_entries.publication_id = publications.publication_id
                    )
                    WHERE state = 'PUBLISHED' AND registry_entry_id IS NULL
                    """
                )
                connection.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS publications_registry_entry_id_idx
                    ON publications (registry_entry_id)
                    WHERE registry_entry_id IS NOT NULL
                    """
                )
                if "profile_hash" not in columns:
                    connection.execute("ALTER TABLE publications ADD COLUMN profile_hash TEXT")
                connection.execute(
                    """
                    UPDATE publications
                    SET profile_hash = (
                        SELECT validations.profile_hash
                        FROM validations
                        WHERE validations.validation_id = publications.validation_id
                    )
                    WHERE profile_hash IS NULL
                    """
                )
                invalid_published = connection.execute(
                    """
                    SELECT publications.publication_id
                    FROM publications
                    LEFT JOIN validations
                      ON validations.validation_id = publications.validation_id
                    LEFT JOIN pbo_certificates
                      ON pbo_certificates.pbo_certificate_id = validations.pbo_certificate_id
                    LEFT JOIN holdout_slots
                      ON holdout_slots.lineage_id = publications.lineage_id
                     AND holdout_slots.consuming_validation_id = publications.validation_id
                    LEFT JOIN registry_entries
                      ON registry_entries.registry_entry_id = publications.registry_entry_id
                     AND registry_entries.publication_id = publications.publication_id
                     AND registry_entries.validation_id = publications.validation_id
                     AND registry_entries.strategy_id = publications.strategy_id
                     AND registry_entries.candidate_hash = publications.candidate_hash
                     AND registry_entries.lineage_id = publications.lineage_id
                    WHERE publications.state = 'PUBLISHED'
                      AND (
                        publications.profile_hash IS NULL
                        OR validations.validation_id IS NULL
                        OR validations.pre_validation_decision != 'PASS'
                        OR validations.profile_hash != publications.profile_hash
                        OR pbo_certificates.pbo_certificate_id IS NULL
                        OR pbo_certificates.decision != 'PASS'
                        OR pbo_certificates.profile_hash != publications.profile_hash
                        OR holdout_slots.lineage_id IS NULL
                        OR holdout_slots.state != 'CONSUMED'
                        OR holdout_slots.profile_hash != publications.profile_hash
                        OR registry_entries.registry_entry_id IS NULL
                      )
                    LIMIT 1
                    """
                ).fetchone()
                if invalid_published is not None:
                    raise StorageError(
                        "legacy PUBLISHED publication has incomplete or mismatched provenance"
                    )
                for statement in FREEZE_TRIGGERS.split("END;"):
                    if statement.strip():
                        connection.execute(f"{statement}END;")

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Generator[sqlite3.Connection, None, None]:
        """Yield a transaction; callers must not perform external work inside it."""
        with self._lock:
            self._require_healthy()
            depth = getattr(self._state, "depth", 0)
            savepoint = f"alpha_foundry_{depth}"
            if depth == 0:
                self._connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            else:
                self._connection.execute(f"SAVEPOINT {savepoint}")
            self._state.depth = depth + 1
            try:
                yield self._connection
            except BaseException:
                try:
                    self._rollback_transaction(depth, savepoint)
                except BaseException as rollback_error:
                    self._quarantined = True
                    raise StorageError(
                        "SQLite transaction rollback finalization failed; store is quarantined"
                    ) from rollback_error
                raise
            else:
                try:
                    if depth == 0:
                        self._connection.commit()
                    else:
                        self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                except BaseException as finalization_error:
                    try:
                        self._rollback_transaction(depth, savepoint)
                    except BaseException as rollback_error:
                        self._quarantined = True
                        raise StorageError(
                            "SQLite transaction finalization failed and the store is quarantined"
                        ) from rollback_error
                    raise finalization_error
            finally:
                self._state.depth = depth

    def _rollback_transaction(self, depth: int, savepoint: str) -> None:
        if depth == 0:
            self._connection.rollback()
            return
        self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")

    def _require_healthy(self) -> None:
        if self._quarantined:
            raise StorageError(
                "SQLite store is quarantined after a transaction finalization failure"
            )

    def fetch_one(self, statement: str, parameters: Sequence[object] = ()) -> sqlite3.Row | None:
        """Read one row without exposing a transaction boundary to callers."""
        with self._lock:
            self._require_healthy()
            row = self._connection.execute(statement, parameters).fetchone()
            return cast(sqlite3.Row | None, row)

    def fetch_all(self, statement: str, parameters: Sequence[object] = ()) -> list[sqlite3.Row]:
        """Read rows without exposing a transaction boundary to callers."""
        with self._lock:
            self._require_healthy()
            rows = self._connection.execute(statement, parameters).fetchall()
            return cast(list[sqlite3.Row], rows)

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._connection.close()
