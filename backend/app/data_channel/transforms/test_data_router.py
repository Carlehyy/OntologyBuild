"""
测试数据 API — 为 REST Connector 提供测试端点。
在 Docker 网络内可通过 http://backend:8000/api/v2/test-data 访问。
"""
from fastapi import APIRouter, Depends

from app.deps import get_current_user

router = APIRouter(dependencies=[Depends(get_current_user)])

TEST_ITEMS = [
    {"id": 1, "name": "REST Product X", "category": "Software", "price": 299.99, "stock": 45},
    {"id": 2, "name": "REST Product Y", "category": "Hardware", "price": 599.00, "stock": 12},
    {"id": 3, "name": "REST Product Z", "category": "Service", "price": 149.50, "stock": 80},
    {"id": 4, "name": "REST Widget A", "category": "Software", "price": 89.00, "stock": 200},
    {"id": 5, "name": "REST Widget B", "category": "Hardware", "price": 349.99, "stock": 30},
    {"id": 6, "name": "REST Gadget C", "category": "Electronics", "price": 79.99, "stock": 150},
]

TEST_USERS = [
    {"user_id": 1001, "name": "Alice Wang", "email": "alice@example.com", "role": "admin", "created": "2024-01-15"},
    {"user_id": 1002, "name": "Bob Li", "email": "bob@example.com", "role": "editor", "created": "2024-02-20"},
    {"user_id": 1003, "name": "Charlie Chen", "email": "charlie@example.com", "role": "viewer", "created": "2024-03-10"},
    {"user_id": 1004, "name": "Diana Zhao", "email": "diana@example.com", "role": "editor", "created": "2024-04-05"},
    {"user_id": 1005, "name": "Eve Sun", "email": "eve@example.com", "role": "admin", "created": "2024-05-18"},
]


@router.get("/items")
def get_items():
    return TEST_ITEMS


@router.get("/users")
def get_users():
    return TEST_USERS
