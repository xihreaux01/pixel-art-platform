#!/usr/bin/env python3
"""Locust load-test file for the Pixel Art Platform.

Install locust first (not included in requirements.txt):
    pip install locust

Run with:
    locust -f scripts/load_test.py --host http://localhost:8000 --users 50 --spawn-rate 5

This defines realistic user behaviour against the public API.
"""

from __future__ import annotations

import uuid

from locust import HttpUser, between, task


class PixelArtUser(HttpUser):
    """Simulated platform user performing typical actions."""

    # Wait 1-3 seconds between tasks to simulate real browsing.
    wait_time = between(1, 3)

    # Populated after register + login.
    access_token: str | None = None
    username: str = ""
    email: str = ""

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def on_start(self) -> None:
        """Register a fresh user and log in to obtain an access token."""
        unique = uuid.uuid4().hex[:12]
        self.username = f"loadtest_{unique}"
        self.email = f"{self.username}@loadtest.local"
        password = f"P@ss{unique}!"

        # Register
        with self.client.post(
            "/api/v1/auth/register",
            json={
                "email": self.email,
                "username": self.username,
                "password": password,
            },
            catch_response=True,
            name="/api/v1/auth/register",
        ) as resp:
            if resp.status_code not in (200, 201):
                resp.failure(f"Registration failed: {resp.status_code}")
                return

        # Login
        with self.client.post(
            "/api/v1/auth/login",
            json={"email": self.email, "password": password},
            catch_response=True,
            name="/api/v1/auth/login",
        ) as resp:
            if resp.status_code == 200:
                data = resp.json()
                self.access_token = data.get("access_token")
            else:
                resp.failure(f"Login failed: {resp.status_code}")

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _auth_headers(self) -> dict[str, str]:
        if self.access_token:
            return {"Authorization": f"Bearer {self.access_token}"}
        return {}

    # ------------------------------------------------------------------ #
    # Tasks -- weighted to reflect realistic traffic distribution
    # ------------------------------------------------------------------ #

    @task(5)
    def browse_marketplace(self) -> None:
        """Browse the marketplace listing page (most common action)."""
        self.client.get(
            "/api/v1/marketplace/",
            name="/api/v1/marketplace/ [browse]",
        )

    @task(3)
    def view_listing(self) -> None:
        """Fetch a specific marketplace listing.

        Uses a random UUID which will likely 404, but exercises the route.
        """
        fake_id = str(uuid.uuid4())
        with self.client.get(
            f"/api/v1/marketplace/{fake_id}",
            catch_response=True,
            name="/api/v1/marketplace/{id} [view]",
        ) as resp:
            # 404 is expected for random UUIDs -- mark as success.
            if resp.status_code in (200, 404):
                resp.success()

    @task(2)
    def check_balance(self) -> None:
        """Check the authenticated user's credit balance."""
        self.client.get(
            "/api/v1/credits/balance",
            headers=self._auth_headers(),
            name="/api/v1/credits/balance",
        )

    @task(1)
    def health_check(self) -> None:
        """Hit the /health endpoint."""
        self.client.get("/health", name="/health")
