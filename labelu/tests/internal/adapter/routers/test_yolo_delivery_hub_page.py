from fastapi.testclient import TestClient


def test_yolo_delivery_hub_route_removed(client: TestClient):
    r = client.get("/yolo-delivery-hub")
    assert r.status_code == 404
