# ADR-0004: Redis server replaced by Valkey; psycopg (LGPL) accepted

Status: accepted (2026-10-28, approved by the maintainer)

## Redis server → Valkey
REQUIREMENTS §3 names Redis. Redis server releases from 7.4 onward are not under a permissive
licence (RSALv2 / SSPLv1, with AGPLv3 added as an option in Redis 8). Shipping the server image in
the offline bundle would bring those terms with it. **Valkey** is the Linux Foundation fork of
Redis 7.2 under BSD-3-Clause. It uses the same protocol and works unchanged with the redis-py
client (MIT), which is the only part our code depends on.

**Proposal:** run `valkey/valkey:8.1.11-alpine` as the `redis` compose service. The service name,
URLs and client code stay "redis", so switching back is a one-line image change.

## psycopg 3 (LGPL-3.0-only)
psycopg is the PostgreSQL driver that SQLAlchemy uses. LGPL permits commercial use and
redistribution when the library is used unmodified and remains replaceable. Python imports it
dynamically and it ships as a separate wheel inside the image. **Accepted**, with the entry in
`NOTICE` and the licence text included in the bundle. The alternative, pg8000 (BSD-3), is slower
and less widely used.
