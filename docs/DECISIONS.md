# Architecture Decisions

## ADR-001 — Python

Core и CLI пишутся на Python 3.14+ из-за сильной экосистемы numerical/GIS/trajectory processing.

Статус: Accepted.

## ADR-002 — FIT-first

FIT — главный input/output формат. GPX — course/reference/export, но не промежуточный canonical representation.

Статус: Accepted.

## ADR-003 — Detector не знает course

Integrity Detector не использует GPX/OSM/course distance.

Причина: настоящий wrong turn на трейле не является GPS corruption.

Статус: Accepted.

## ADR-004 — Physical plausibility first

Основание для corruption — нарушение физической непрерывности, а не «красивость» трека.

Статус: Accepted.

## ADR-005 — Timestamps immutable during GNSS repair

Невозможная GPS-скорость не исправляется растягиванием времени.

Статус: Accepted.

## ADR-006 — Long spoofing islands are first-class

Detector должен анализировать интервалы, а не только локальные spikes.

Статус: Accepted.

## ADR-007 — Reconstruction separate from detection

Detection выдаёт corrupted interval; reconstruction отдельно выбирает способ восстановления.

Статус: Accepted.

## ADR-008 — Conservative auto-repair

LOW/MEDIUM confidence не должен автоматически менять FIT.

Статус: Accepted.

## ADR-009 — No AI in core

Алгоритм deterministic/offline.

Статус: Accepted.

## ADR-010 — OSM/routing postponed

Map-based reconstruction не входит в v0.1.

Статус: Accepted.

## ADR-011 — Private user fixtures

Реальные FIT пользователя не коммитить в public repo по умолчанию.

Статус: Accepted.
