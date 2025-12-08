CREATE TABLE dockerfiles (
    repo_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    dockerfile TEXT NOT NULL,
    env_agent_log TEXT NOT NULL,
    PRIMARY KEY (repo_id, commit_sha)
);

CREATE TABLE removed_features (
    repo_id TEXT NOT NULL,
    commit_sha TEXT NOT NULL,
    topic TEXT NOT NULL,
    feature_removal_agent_log TEXT NOT NULL,
    src_diff TEXT NOT NULL,
    test_diff TEXT NOT NULL,
    PRIMARY KEY (repo_id, commit_sha, topic)
);
