"""
Locust test plan for ShopAPI.

Users are built on FastHttpUser (geventhttpclient), which sustains an order of
magnitude more requests per second per worker than the default requests-based
client — the right choice when a single load generator has to push serious RPS.

Two user populations (3:1 ratio, mirroring real traffic):

  * BrowsingUser    — anonymous-style browsing: product lists, detail pages, search
  * PurchasingUser  — full conversion funnel as a strict sequence:
                      browse -> product page -> add to cart -> checkout

Load profile is selected with the LOAD_SHAPE environment variable:

  (unset)             classic -u/-r/-t CLI flags
  LOAD_SHAPE=stages   stepped stress ramp: 10 -> 50 -> 100 -> 150 users
  LOAD_SHAPE=spike    steady baseline with a sudden 10x traffic spike
  LOAD_SHAPE=soak     ramp up once, then hold for SOAK_MINUTES (default 30)
"""

import os
import random
import uuid

from locust import LoadTestShape, SequentialTaskSet, between, events, tag, task
from locust.contrib.fasthttp import FastHttpUser

SEARCH_TERMS = ["book", "pro", "sport", "home", "electro", "product 1", "product 42"]
SLOW_REQUEST_MS = int(os.getenv("SLOW_REQUEST_MS", "1000"))


def login(client) -> dict:
    """Authenticate and return auth + session-correlation headers.

    X-Session-ID ties every request of one virtual user together, so load test
    traffic can be correlated with the target system's logs and traces.
    """
    username = f"user_{random.randint(1, 100_000)}"
    resp = client.post("/auth/login", json={"username": username, "password": "perf-demo"})
    token = resp.json().get("token", "") if resp.status_code == 200 else ""
    return {"Authorization": f"Bearer {token}", "X-Session-ID": uuid.uuid4().hex}


class BrowsingUser(FastHttpUser):
    """Window shopper: generates the bulk of read traffic, never buys."""

    weight = 3
    wait_time = between(1, 3)

    @task(4)
    @tag("catalog")
    def browse_catalog(self):
        page = random.randint(1, 10)
        self.client.get(f"/products?page={page}", name="/products?page=[n]")

    @task(3)
    @tag("catalog")
    def view_product(self):
        product_id = random.randint(1, 200)
        with self.client.get(
            f"/products/{product_id}", name="/products/[id]", catch_response=True
        ) as resp:
            if resp.status_code == 200 and "price" not in resp.text:
                resp.failure("Product payload missing 'price' field")

    @task(2)
    @tag("search")
    def search(self):
        term = random.choice(SEARCH_TERMS)
        self.client.get(f"/search?q={term}", name="/search?q=[term]")


class CheckoutJourney(SequentialTaskSet):
    """The conversion funnel — every step runs in order, like a real buyer."""

    @task
    def browse(self):
        self.client.get("/products?page=1", name="/products?page=[n]")

    @task
    def view_product(self):
        self.product_id = random.randint(1, 200)
        self.client.get(f"/products/{self.product_id}", name="/products/[id]")

    @task
    def add_to_cart(self):
        self.client.post(
            "/cart/items",
            json={"product_id": self.product_id, "quantity": random.randint(1, 3)},
            headers=self.user.auth_headers,
        )

    @task
    def checkout(self):
        with self.client.post(
            "/checkout",
            json={"items": [{"product_id": self.product_id, "quantity": 1}]},
            headers=self.user.auth_headers,
            catch_response=True,
        ) as resp:
            if resp.status_code == 200 and not resp.json().get("order_id"):
                resp.failure("Checkout returned 200 but no order_id")


class PurchasingUser(FastHttpUser):
    """Buyer: logs in once, then loops through the checkout funnel."""

    weight = 1
    wait_time = between(2, 5)
    tasks = [CheckoutJourney]

    def on_start(self):
        self.auth_headers = login(self.client)


@events.request.add_listener
def log_slow_requests(request_type, name, response_time, response_length, exception, **kwargs):
    """Surface individual outliers in the console, not just aggregate stats."""
    if exception is None and response_time > SLOW_REQUEST_MS:
        print(f"SLOW  {request_type} {name}  {response_time:.0f} ms")


# --------------------------------------------------------------------------
# Load shapes (activated with LOAD_SHAPE=stages|spike|soak)
# --------------------------------------------------------------------------

_SHAPE = os.getenv("LOAD_SHAPE", "").strip().lower()

if _SHAPE == "stages":

    class StressRamp(LoadTestShape):
        """Stepped ramp to find the saturation point."""

        stages = [
            (60, 10, 1),    # warm-up
            (180, 50, 2),
            (300, 100, 3),
            (420, 150, 3),
            (480, 0, 5),    # ramp-down
        ]

        def tick(self):
            run_time = self.get_run_time()
            for end_time, users, spawn_rate in self.stages:
                if run_time < end_time:
                    return (users, spawn_rate)
            return None

elif _SHAPE == "spike":

    class SpikeShape(LoadTestShape):
        """Steady 20-user baseline with a sudden 10x spike (e.g. flash sale)."""

        def tick(self):
            run_time = self.get_run_time()
            if run_time < 120:
                return (20, 5)
            if run_time < 180:
                return (200, 50)   # the spike
            if run_time < 360:
                return (20, 50)    # recovery — watch how fast p95 settles
            return None

elif _SHAPE == "soak":

    class SoakShape(LoadTestShape):
        """Constant moderate load over a long period to expose leaks/drift."""

        hold_minutes = int(os.getenv("SOAK_MINUTES", "30"))

        def tick(self):
            run_time = self.get_run_time()
            if run_time < 120:
                return (60, 1)
            if run_time < 120 + self.hold_minutes * 60:
                return (60, 10)
            return None
