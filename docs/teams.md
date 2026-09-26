# Teamverwaltung

Abteilungsleitungen können die Rolle und Kanäle ihrer Teams mit Discord-Befehlen erstellen, umbenennen und löschen. Der Bot erkennt die Abteilung anhand der Leitungsrolle. Wer mehrere Abteilungen leitet, wählt beim Befehl zusätzlich `department` aus.

## Befehle

| Befehl | Beschreibung | Beispiel |
|---|---|---|
| `/team create <name> [department]` | Erstellt die Teamrolle sowie die Text- und Sprachkanäle. | `/team create name:Fire` |
| `/team rename <team> <new_name> [department]` | Benennt die Teamrolle und alle zugehörigen Kanäle um. | `/team rename team:Fire new_name:Flame` |
| `/team delete <team> [department]` | Löscht die Teamrolle und alle zugehörigen Kanäle nach einer Bestätigung. | `/team delete team:Flame` |

> [!WARNING]
> Beim Löschen gehen die Teamkanäle und deren Nachrichten endgültig verloren.

## Rollen und Kanäle

Normale Teamrollen heißen `<Abteilung> Team <Teamname>`, zum Beispiel `CS Team Fire`. Für Main- und Academy-Teams wird die Reihenfolge angepasst: `CS Main Team` und `CS Academy Team`.

Neue Teamrollen können von allen erwähnt werden und werden automatisch bei den anderen Rollen ihrer Abteilung einsortiert.

Für Valorant werden vier Textkanäle erstellt:

- `<team>-termine`
- `<team>-chat`
- `<team>-vods`
- `<team>-strats`

Darüber wird ein Trenner wie `━━━━fire━━━━` angelegt.

## Sichtbarkeit

- Textkanäle: sichtbar für Teamrolle und zuständige Leitungsrolle.
- Sprachkanal: Teamrolle und Leitung dürfen sehen und beitreten. Die Abteilungsrolle darf den Kanal sehen, aber nicht beitreten.
- Andere Mitglieder können die Kanäle nicht sehen.

## Bestehende Teams

Bestehende Teams können ebenfalls mit `/team rename` und `/team delete` verwaltet werden, wenn ihre Rolle und Kanäle wie oben beschrieben benannt sind. Wenn der Bot ein Team nicht eindeutig erkennt, nimmt er keine Änderungen vor und zeigt eine Fehlermeldung an.
