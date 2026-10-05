# Changelog

Alle nennenswerten Änderungen am **mailarc-server** (zentrale API/DB der Produktfamilie
*mailarc*) werden in dieser Datei dokumentiert.

Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.0.0/),
die Versionierung ist vierstellig (PEP 440).

## [Unreleased]

### In Arbeit
- **Favoriten / Gespeicherte Suchen (F1)** – neue Tabelle `saved_search` und client-API
  `GET/POST /api/searches`, `PATCH/DELETE /api/searches/{id}` sowie `POST /api/searches/{id}/run`
  (Sofort-Ausführung über dieselben Filter wie `/api/search`). Benutzergebunden,
  Sichtbarkeit standardmäßig privat.

## [2.6.4.0] – 2026-09-30

### Hinzugefügt
- `mailarc-web` wird same-origin direkt vom Server ausgeliefert (statisches Bundle unter `/`).

## [2.6.3.0] – 2026-09-29

### Hinzugefügt
- Read-only Elasticsearch-Such-Proxy für die CLI: `POST /es/search`, `POST /es/count`.

## [2.6.2.0] – 2026-09-28

### Hinzugefügt
- Asynchrone serverseitige Indexierung über `POST /index-jobs` (Jobs, Anhang-Extraktion, Index-Dokumente).

## [2.6.1.0] – 2026-08-09

### Hinzugefügt
- Self-Service-Passwortwechsel in der client-API.

## [2.6.0.0] – 2026-08-09

### Hinzugefügt
- Web-Benutzerverwaltung mit Rollen (Admin/Benutzer).

## [2.5.0.0] – 2026-08-08

### Hinzugefügt
- `/api/accounts` – cookie-authentifizierte Kontenverwaltung fürs Web-UI.

## [2.4.1.0] – 2026-08-08

### Geändert
- Bei nicht erreichbarem Elasticsearch wird `503` statt `500` zurückgegeben.

## [2.4.0.0] – 2026-08-08

### Geändert
- `WEB_*`/`ES_*` per `env_file` in den Container; client-API dokumentiert.

## [2.3.0.0] – 2026-08-04

### Hinzugefügt
- Zentrale Komplett-Konfiguration je Konto (ES + Anhang-Einstellungen).

[Unreleased]: https://github.com/saggi1967/mailarc-server/compare/v2.6.4.0...HEAD
[2.6.4.0]: https://github.com/saggi1967/mailarc-server/releases/tag/v2.6.4.0
[2.6.3.0]: https://github.com/saggi1967/mailarc-server/releases/tag/v2.6.3.0
[2.6.2.0]: https://github.com/saggi1967/mailarc-server/releases/tag/v2.6.2.0
[2.6.1.0]: https://github.com/saggi1967/mailarc-server/releases/tag/v2.6.1.0
[2.6.0.0]: https://github.com/saggi1967/mailarc-server/releases/tag/v2.6.0.0
[2.5.0.0]: https://github.com/saggi1967/mailarc-server/releases/tag/v2.5.0.0
[2.4.1.0]: https://github.com/saggi1967/mailarc-server/releases/tag/v2.4.1.0
[2.4.0.0]: https://github.com/saggi1967/mailarc-server/releases/tag/v2.4.0.0
[2.3.0.0]: https://github.com/saggi1967/mailarc-server/releases/tag/v2.3.0.0
