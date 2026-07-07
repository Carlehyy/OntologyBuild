"""
正规本体模型 + 数据采集 端到端测试

流程：登录 → 建本体 → 建对象类型/链接/接口/函数/动作 → 采集 AI HOT 真实数据
      → 测试函数 → 执行动作 → 拉取 full ontology 校验。
直接打 HTTP，验证全链路。
"""
import sys
import httpx

BASE = "http://localhost:8000"


def main():
    c = httpx.Client(base_url=BASE, timeout=40)

    # 1. login
    r = c.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    assert r.status_code == 200, r.text
    token = r.json()["data"]["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    print("✓ login")

    # 2. create ontology
    import time
    name = f"AI资讯本体_E2E_{int(time.time())}"
    r = c.post("/api/v1/ontologies", headers=h,
               json={"name": name, "domain": "科技", "description": "E2E", "build_mode": "simple_llm"})
    assert r.status_code == 201, r.text
    oid = r.json()["data"]["id"]
    print(f"✓ ontology {oid}")

    F = f"/api/v2/formal/ontologies/{oid}"

    # 3. interface
    r = c.post(f"{F}/interfaces", headers=h, json={
        "name": "Timestamped", "displayName": "可追溯",
        "properties": [{"id": "publishedAt", "name": "publishedAt", "displayName": "发布时间",
                        "type": "datetime", "required": False}]})
    assert r.status_code == 201, r.text
    iface_id = r.json()["data"]["id"]
    print("✓ interface")

    # 4. object type: 资讯条目
    news_props = [
        {"id": "title", "name": "title", "displayName": "标题", "type": "string", "required": True},
        {"id": "source", "name": "source", "displayName": "信源", "type": "string", "required": False},
        {"id": "category", "name": "category", "displayName": "分类", "type": "string", "required": False},
        {"id": "score", "name": "score", "displayName": "评分", "type": "number", "required": False},
        {"id": "selected", "name": "selected", "displayName": "精选", "type": "boolean", "required": False},
        {"id": "publishedAt", "name": "publishedAt", "displayName": "发布时间", "type": "datetime", "required": False},
        {"id": "summary", "name": "summary", "displayName": "摘要", "type": "string", "required": False},
    ]
    r = c.post(f"{F}/object-types", headers=h, json={
        "name": "NewsItem", "displayName": "资讯条目", "icon": "📰", "color": "#6366f1",
        "primaryKey": "title", "properties": news_props, "interfaces": [iface_id]})
    assert r.status_code == 201, r.text
    news_ot = r.json()["data"]["id"]
    print("✓ object-type NewsItem")

    # source object type
    r = c.post(f"{F}/object-types", headers=h, json={
        "name": "Source", "displayName": "信源", "icon": "🛰️", "color": "#10b981",
        "primaryKey": "name",
        "properties": [{"id": "name", "name": "name", "displayName": "名称", "type": "string", "required": True}]})
    src_ot = r.json()["data"]["id"]
    print("✓ object-type Source")

    # 5. link type: 条目 -属于-> 信源
    r = c.post(f"{F}/link-types", headers=h, json={
        "name": "fromSource", "displayName": "来自信源",
        "sourceObjectTypeId": news_ot, "targetObjectTypeId": src_ot,
        "cardinality": "many-to-one"})
    assert r.status_code == 201, r.text
    link_id = r.json()["data"]["id"]
    print("✓ link-type")

    # 6. function: 高分判定 (expression)
    r = c.post(f"{F}/functions", headers=h, json={
        "name": "isHighScore", "displayName": "是否高分", "functionType": "query",
        "language": "expression", "returnType": "boolean",
        "parameters": [{"id": "p", "name": "threshold", "type": "number", "required": True}],
        "body": "params.threshold <= (object.score if object.score else 0)"})
    assert r.status_code == 201, r.text
    fn_id = r.json()["data"]["id"]
    print("✓ function")

    # validation function for action
    r = c.post(f"{F}/functions", headers=h, json={
        "name": "validateSelect", "displayName": "精选校验", "functionType": "action_validation",
        "language": "expression", "returnType": "validation_result",
        "body": "{'valid': (object.score if object.score else 0) >= 60, 'errors': [] if (object.score if object.score else 0) >= 60 else ['分数低于60不可精选']}"})
    val_fn = r.json()["data"]["id"]
    print("✓ validation function")

    # 7. action: 精选条目
    r = c.post(f"{F}/actions", headers=h, json={
        "name": "markSelected", "displayName": "标记精选", "objectTypeId": news_ot,
        "validationFunctionId": val_fn,
        "parameters": [],
        "rules": [{"id": "r1", "type": "update_property", "name": "设为精选", "enabled": True, "order": 0,
                   "config": {"type": "update_property", "targetProperty": "selected",
                              "valueSource": "constant", "value": "true"}}]})
    assert r.status_code == 201, r.text
    action_id = r.json()["data"]["id"]
    print("✓ action")

    # 8. collect AI HOT real data
    r = c.post(f"/api/v2/collectors/aihot/collect/{oid}", headers=h, json={
        "object_type_id": news_ot, "mode": "selected", "take": 15,
        "source_object_type_id": src_ot, "source_link_type_id": link_id})
    assert r.status_code == 200, r.text
    stat = r.json()["data"]
    print(f"✓ collect AI HOT: {stat}")
    assert stat["created"] > 0, "应采集到真实数据"

    # 9. list instances
    r = c.get(f"{F}/instances?object_type_id={news_ot}", headers=h)
    insts = r.json()["data"]
    assert len(insts) > 0
    print(f"✓ instances: {len(insts)}")
    sample = insts[0]

    # 10. test function on a real instance
    r = c.post(f"{F}/test-function", headers=h, json={
        "function_id": fn_id, "params": {"threshold": 50}, "object_instance_id": sample["id"]})
    assert r.status_code == 200, r.text
    fr = r.json()["data"]
    print(f"✓ test-function isHighScore(50) on '{sample['properties'].get('title','')[:20]}': {fr.get('result')} (success={fr.get('success')})")
    assert fr["success"], fr

    # 11. run action (dry run then real)
    r = c.post(f"{F}/run-action", headers=h, json={
        "action_id": action_id, "parameters": {}, "target_instance_id": sample["id"], "dry_run": True})
    log = r.json()["data"]
    print(f"✓ run-action dryRun status={log['status']} effects={len(log.get('effects',[]))}")

    r = c.post(f"{F}/run-action", headers=h, json={
        "action_id": action_id, "parameters": {}, "target_instance_id": sample["id"], "dry_run": False})
    log = r.json()["data"]
    print(f"✓ run-action status={log['status']}")

    # 12. full ontology
    r = c.get(f"{F}/full", headers=h)
    full = r.json()["data"]
    print(f"✓ full ontology: objectTypes={len(full['objectTypes'])} linkTypes={len(full['linkTypes'])} "
          f"functions={len(full['functions'])} actions={len(full['actions'])} "
          f"instances={len(full['instances'])} linkInstances={len(full['linkInstances'])} "
          f"logs={len(full['executionLogs'])}")
    assert len(full["objectTypes"]) == 2
    assert len(full["instances"]) > 0
    assert len(full["linkInstances"]) > 0

    print("\n🎉 ALL E2E CHECKS PASSED")
    print(f"   ontology_id = {oid}")
    return oid


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\n❌ ASSERTION FAILED: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"\n❌ ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
