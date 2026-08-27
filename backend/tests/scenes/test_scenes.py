"""三维场景域 API 测试：CRUD、草稿/发布状态机、快照克隆、版本冻结与运行日志。"""

VALID_DEFINITION = {
    "meta": {"id": "demo-park", "name": "演示园区", "version": "0.1.0"},
    "stage": {"camera": {"pos": [92, 78, 92], "target": [0, 0, 0], "fov": 30}},
    "objects": [
        {
            "id": "office",
            "label": "办公楼",
            "type": "office",
            "layout": {"x": -20, "z": 0, "w": 12, "d": 10, "h": 16},
        },
        {
            "id": "warehouse",
            "label": "仓库",
            "type": "warehouse",
            "layout": {"x": 26, "z": -10, "w": 14, "d": 20, "h": 18},
        },
    ],
    "relations": [{"from": "office", "to": "warehouse", "kind": "flow"}],
    "dataBindings": [
        {
            "target": "warehouse",
            "source": "client",
            "path": "wh.rate",
            "rules": [
                {"when": "> 95", "status": "alarm", "message": "库位告急"},
                {"when": "else", "status": "normal"},
            ],
        },
    ],
}

INVALID_DEFINITION = {
    "meta": {"id": "Bad_Id", "name": "", "version": ""},
    "objects": [
        {"id": "dup", "label": "A", "type": "castle",
         "layout": {"x": 0, "z": 0, "w": 1, "d": 1, "h": 1}},
        {"id": "dup", "label": "B", "type": "office",
         "layout": {"x": 0, "z": 0, "w": 1, "d": 1, "h": 1}},
    ],
}


def _create_scene(client, auth_headers, name="测试场景", **kwargs):
    payload = {"name": name}
    payload.update(kwargs)
    resp = client.post("/api/v2/scenes", json=payload, headers=auth_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _save_definition(client, auth_headers, scene_id, definition=None, note=""):
    body = {
        "definition": VALID_DEFINITION if definition is None else definition,
        "note": note,
    }
    return client.put(
        f"/api/v2/scenes/{scene_id}/definition", json=body, headers=auth_headers)


# —— CRUD ——

def test_create_scene_defaults_to_draft_without_versions(client, auth_headers):
    scene = _create_scene(client, auth_headers)
    assert scene["status"] == "draft"
    assert scene["current_version_no"] == 0
    assert scene["published_version_no"] is None
    assert scene["version_count"] == 0


def test_create_with_definition_freezes_v1(client, auth_headers):
    scene = _create_scene(
        client, auth_headers, definition=VALID_DEFINITION)
    assert scene["current_version_no"] == 1
    assert scene["version_count"] == 1


def test_get_scene_detail_returns_existing_scene(client, auth_headers):
    scene = _create_scene(client, auth_headers, definition=VALID_DEFINITION)
    resp = client.get(f"/api/v2/scenes/{scene['id']}", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["id"] == scene["id"]
    assert data["name"] == scene["name"]
    assert data["status"] == "draft"
    assert data["current_version_no"] == 1
    assert data["version_count"] == 1


def test_create_rejects_invalid_definition_atomically(client, auth_headers):
    resp = client.post(
        "/api/v2/scenes",
        json={"name": "坏定义场景", "definition": INVALID_DEFINITION},
        headers=auth_headers,
    )
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert detail["code"] == "invalid_scene_definition"
    paths = {issue["path"] for issue in detail["issues"]}
    assert "meta.id" in paths and "objects[1].id" in paths
    # 原子性：校验失败不落任何数据
    listing = client.get("/api/v2/scenes", headers=auth_headers).json()["data"]
    assert listing["total"] == 0


def test_update_basic_info_does_not_touch_definition_state(client, auth_headers):
    scene = _create_scene(client, auth_headers)
    resp = client.patch(
        f"/api/v2/scenes/{scene['id']}",
        json={"description": "新描述", "icon": "boxes"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["description"] == "新描述"
    assert data["status"] == "draft"
    assert data["current_version_no"] == 0


def test_delete_scene_removes_versions_and_returns_404_after(client, auth_headers):
    scene = _create_scene(client, auth_headers, definition=VALID_DEFINITION)
    resp = client.delete(f"/api/v2/scenes/{scene['id']}", headers=auth_headers)
    assert resp.status_code == 204
    gone = client.get(f"/api/v2/scenes/{scene['id']}", headers=auth_headers)
    assert gone.status_code == 404
    assert gone.json()["detail"]["code"] == "scene_not_found"
    versions = client.get(
        f"/api/v2/scenes/{scene['id']}/versions", headers=auth_headers)
    assert versions.status_code == 404


def test_scenes_require_authentication(client):
    resp = client.get("/api/v2/scenes")
    assert resp.status_code in (401, 403)


# —— 列表过滤 ——

def test_list_scenes_supports_q_and_status_filters(client, auth_headers):
    _create_scene(client, auth_headers, name="供应链园区")
    _create_scene(client, auth_headers, name="能源场站")
    data = client.get(
        "/api/v2/scenes", params={"q": "园区"}, headers=auth_headers,
    ).json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["name"] == "供应链园区"
    data = client.get(
        "/api/v2/scenes", params={"status": "published"}, headers=auth_headers,
    ).json()["data"]
    assert data["total"] == 0
    bad = client.get(
        "/api/v2/scenes", params={"status": "bogus"}, headers=auth_headers)
    assert bad.status_code == 400


# —— 版本冻结 ——

def test_save_definition_freezes_versions_and_normalizes_flows(client, auth_headers):
    scene = _create_scene(client, auth_headers)
    first = _save_definition(client, auth_headers, scene["id"], note="初版")
    assert first.status_code == 200
    legacy = dict(VALID_DEFINITION)
    legacy.pop("relations")
    legacy["flows"] = [["office", "warehouse"]]
    second = _save_definition(client, auth_headers, scene["id"], definition=legacy, note="旧写法")
    assert second.status_code == 200
    data = second.json()["data"]
    assert data["scene"]["current_version_no"] == 2
    stored = data["version"]["definition"]
    # flows 已归一为 relations，且不再保留 flows 键
    assert stored["relations"] == [
        {"from": "office", "to": "warehouse", "kind": "flow"}]
    assert "flows" not in stored
    versions = client.get(
        f"/api/v2/scenes/{scene['id']}/versions", headers=auth_headers,
    ).json()["data"]
    assert [v["version_no"] for v in versions["items"]] == [2, 1]
    detail = client.get(
        f"/api/v2/scenes/{scene['id']}/versions/1", headers=auth_headers,
    ).json()["data"]
    assert detail["definition"]["meta"]["id"] == "demo-park"


def test_save_definition_rejects_invalid_input(client, auth_headers):
    scene = _create_scene(client, auth_headers)
    resp = _save_definition(
        client, auth_headers, scene["id"], definition=INVALID_DEFINITION)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "invalid_scene_definition"


# —— 状态机：发布 / 编辑回落 / 重新发布 ——

def test_publish_edit_demote_and_republish_lifecycle(client, auth_headers):
    scene = _create_scene(client, auth_headers)
    empty = client.post(
        f"/api/v2/scenes/{scene['id']}/publish", headers=auth_headers)
    assert empty.status_code == 400
    assert empty.json()["detail"]["code"] == "scene_no_version"

    _save_definition(client, auth_headers, scene["id"], note="v1")
    published = client.post(
        f"/api/v2/scenes/{scene['id']}/publish", headers=auth_headers)
    assert published.status_code == 200
    state = published.json()["data"]
    assert state["status"] == "published"
    assert state["published_version_no"] == 1

    repeat = client.post(
        f"/api/v2/scenes/{scene['id']}/publish", headers=auth_headers)
    assert repeat.status_code == 400
    assert repeat.json()["detail"]["code"] == "scene_already_published"

    demoted = _save_definition(client, auth_headers, scene["id"], note="v2")
    state = demoted.json()["data"]["scene"]
    # 发布态被继续编辑 → 回到草稿态；已发布版本保留可回看/重发布
    assert state["status"] == "draft"
    assert state["published_version_no"] == 1
    assert state["current_version_no"] == 2

    republished = client.post(
        f"/api/v2/scenes/{scene['id']}/publish", headers=auth_headers)
    state = republished.json()["data"]
    assert state["status"] == "published"
    assert state["published_version_no"] == 2


# —— 快照克隆 ——

def test_clone_from_published_uses_published_snapshot(client, auth_headers):
    scene = _create_scene(client, auth_headers)
    _save_definition(client, auth_headers, scene["id"], note="v1")
    changed = dict(VALID_DEFINITION)
    changed["meta"] = dict(changed["meta"], version="0.2.0")
    _save_definition(client, auth_headers, scene["id"], definition=changed, note="v2")
    client.post(f"/api/v2/scenes/{scene['id']}/publish", headers=auth_headers)
    third = dict(VALID_DEFINITION)
    third["meta"] = dict(third["meta"], version="0.3.0-draft")
    _save_definition(client, auth_headers, scene["id"], definition=third, note="v3 草稿")

    resp = client.post(
        f"/api/v2/scenes/{scene['id']}/clone", headers=auth_headers)
    assert resp.status_code == 201
    cloned = resp.json()["data"]
    assert cloned["id"] != scene["id"]
    assert cloned["name"] == "测试场景-副本"
    assert cloned["status"] == "draft"
    assert cloned["current_version_no"] == 1
    assert cloned["published_version_no"] is None
    snapshot = client.get(
        f"/api/v2/scenes/{cloned['id']}/versions/1", headers=auth_headers,
    ).json()["data"]
    # 发布态克隆取「已发布版本」快照，而非最新草稿 v3
    assert snapshot["definition"]["meta"]["version"] == "0.2.0"
    assert snapshot["source"] == "clone"
    assert "测试场景" in snapshot["note"]


def test_clone_requires_existing_definition(client, auth_headers):
    scene = _create_scene(client, auth_headers)
    resp = client.post(
        f"/api/v2/scenes/{scene['id']}/clone", headers=auth_headers)
    assert resp.status_code == 400
    assert resp.json()["detail"]["code"] == "scene_not_clonable"


# —— 扩展词汇：events 与 ontology_concept_id ——

def test_valid_events_saved_and_clone_carries_extension(client, auth_headers):
    scene = _create_scene(client, auth_headers)
    definition = dict(VALID_DEFINITION)
    definition["objects"] = [
        {
            "id": "office",
            "label": "办公楼",
            "type": "office",
            "layout": {"x": -20, "z": 0, "w": 12, "d": 10, "h": 16},
            "ontology_concept_id": "concept-office-building",
        },
        VALID_DEFINITION["objects"][1],
    ]
    definition["events"] = [
        {"key": "inventory-alarm", "label": "库位告急",
         "objectId": "warehouse", "description": "库位利用率超过阈值"},
    ]
    saved = _save_definition(
        client, auth_headers, scene["id"], definition=definition)
    assert saved.status_code == 200

    cloned = client.post(
        f"/api/v2/scenes/{scene['id']}/clone", headers=auth_headers)
    assert cloned.status_code == 201
    snapshot = client.get(
        f"/api/v2/scenes/{cloned.json()['data']['id']}/versions/1",
        headers=auth_headers,
    ).json()["data"]
    # 克隆取当前生效定义快照，扩展词汇原样随行
    assert snapshot["definition"]["events"] == definition["events"]
    assert (
        snapshot["definition"]["objects"][0]["ontology_concept_id"]
        == "concept-office-building"
    )


def test_invalid_events_rejected_with_expected_issues(client, auth_headers):
    scene = _create_scene(client, auth_headers)

    def save_bad(events):
        resp = _save_definition(
            client, auth_headers, scene["id"],
            definition=dict(VALID_DEFINITION, events=events))
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["code"] == "invalid_scene_definition"
        return {issue["path"]: issue["message"] for issue in detail["issues"]}

    # key 重复（第二个事件）
    dup = save_bad([
        {"key": "stock-short", "label": "库存不足", "objectId": "office"},
        {"key": "stock-short", "label": "库存告急", "objectId": "office"},
    ])
    assert dup["events[1].key"] == "事件 key 重复：stock-short"

    # objectId 引用不存在的对象
    ghost = save_bad([
        {"key": "ghost-event", "label": "幽灵事件", "objectId": "no-such-object"},
    ])
    assert ghost["events[0].objectId"] == "引用了不存在的对象 id：no-such-object"

    # label 超长（>80）
    long_label = save_bad([{"key": "long-label-event", "label": "长" * 81}])
    assert long_label["events[0].label"] == "长度不能超过 80"

    # 缺少 label
    missing_label = save_bad([{"key": "no-label-event"}])
    assert missing_label["events[0].label"] == "必须是非空字符串"


def test_invalid_ontology_concept_id_rejected(client, auth_headers):
    scene = _create_scene(client, auth_headers)
    bad_objects = [
        dict(VALID_DEFINITION["objects"][0], ontology_concept_id=""),
        VALID_DEFINITION["objects"][1],
    ]
    resp = _save_definition(
        client, auth_headers, scene["id"],
        definition=dict(VALID_DEFINITION, objects=bad_objects))
    assert resp.status_code == 400
    issues = {
        (issue["path"], issue["message"])
        for issue in resp.json()["detail"]["issues"]
    }
    assert ("objects[0].ontology_concept_id", "必须是非空字符串") in issues


# —— 运行日志 ——

def test_runtime_logs_append_query_and_atomic_validation(client, auth_headers):
    scene = _create_scene(client, auth_headers)
    entries = [
        {"level": "info", "object_id": "office", "event_key": "binding.rule",
         "message": "指标恢复 normal"},
        {"level": "alarm", "object_id": "warehouse", "event_key": "binding.rule",
         "message": "库位利用率 > 95%", "payload": {"value": 97.2}},
        {"level": "warning", "object_id": "warehouse", "event_key": "binding.rule",
         "message": "库位利用率 > 85%"},
    ]
    resp = client.post(
        f"/api/v2/scenes/{scene['id']}/runtime-logs",
        json={"entries": entries}, headers=auth_headers)
    assert resp.status_code == 201
    assert resp.json()["data"]["appended"] == 3

    listed = client.get(
        f"/api/v2/scenes/{scene['id']}/runtime-logs", headers=auth_headers,
    ).json()["data"]
    assert listed["total"] == 3
    filtered = client.get(
        f"/api/v2/scenes/{scene['id']}/runtime-logs",
        params={"level": "alarm"}, headers=auth_headers).json()["data"]
    assert filtered["total"] == 1
    assert filtered["items"][0]["payload"] == {"value": 97.2}

    bad_batch = entries + [{"level": "fatal", "message": "未知级别"}]
    rejected = client.post(
        f"/api/v2/scenes/{scene['id']}/runtime-logs",
        json={"entries": bad_batch}, headers=auth_headers)
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["code"] == "invalid_log_level"
    # 整批原子：拒绝后不产生半批数据
    after = client.get(
        f"/api/v2/scenes/{scene['id']}/runtime-logs", headers=auth_headers,
    ).json()["data"]
    assert after["total"] == 3

    overflow = client.post(
        f"/api/v2/scenes/{scene['id']}/runtime-logs",
        json={"entries": [{"level": "info", "message": "x"}] * 201},
        headers=auth_headers)
    assert overflow.status_code == 400
    assert overflow.json()["detail"]["code"] == "too_many_log_entries"


def test_runtime_logs_pruned_to_keep_limit(client, auth_headers, db):
    from datetime import datetime, timedelta, timezone

    from app.scenes import models as scenes_models

    scene = _create_scene(client, auth_headers)
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    history = [
        scenes_models.SceneRuntimeLog(
            scene_id=scene["id"],
            level="info",
            event_key="seed.history",
            message=f"历史日志 {index}",
            occurred_at=base_time + timedelta(minutes=index),
        )
        for index in range(scenes_models.RUNTIME_LOG_KEEP + 200)
    ]
    db.add_all(history)
    db.commit()

    # 再走一次服务端上报：当前批次入库后触发保留上限裁剪
    newest_time = base_time + timedelta(days=365)
    resp = client.post(
        f"/api/v2/scenes/{scene['id']}/runtime-logs",
        json={"entries": [{
            "level": "alarm", "message": "最新日志",
            "occurred_at": newest_time.isoformat(),
        }]},
        headers=auth_headers,
    )
    assert resp.status_code == 201

    remaining = db.query(scenes_models.SceneRuntimeLog).filter(
        scenes_models.SceneRuntimeLog.scene_id == scene["id"],
    ).all()
    assert len(remaining) == scenes_models.RUNTIME_LOG_KEEP
    messages = [row.message for row in remaining]
    assert "历史日志 0" not in messages      # 超限的最老记录被物理删除
    assert "历史日志 201" in messages        # 保留窗口内的边界行仍在
    assert "最新日志" in messages            # 最新上报必被保留


def test_get_unknown_scene_returns_structured_404(client, auth_headers):
    resp = client.get("/api/v2/scenes/no-such-id", headers=auth_headers)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "scene_not_found"
