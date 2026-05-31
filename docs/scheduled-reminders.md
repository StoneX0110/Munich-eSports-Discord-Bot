# 📣 Wiederkehrende Reminder

Automatische Nachrichten mit Rollen-Ping für regelmäßige Team-Termine, Fristen oder organisatorische Hinweise. **Abteilungsleiter oder Staff** richten Reminder ein. Der Bot sendet die konfigurierte Nachricht jede Woche zur gewählten Zeit in dem Kanal, in dem der Reminder erstellt wurde.

## Ablauf

`/scheduled-reminder create` → wöchentlicher Rollen-Ping mit Nachricht → automatische Wiederholung jede Woche

---

## Befehle (für Abteilungsleiter oder Staff)

| Befehl | Beschreibung | Beispiel |
|--------|-------------|---------|
| `/scheduled-reminder create <role> <weekday> <hour> <message>` | Erstellt einen wöchentlichen Reminder für `role`. Der Reminder wird jeden `weekday` zur vollen Stunde `hour` gesendet. | `/scheduled-reminder create role:@CS Main Team weekday:Mittwoch hour:18 message:Bitte Trainingsverfügbarkeit prüfen.` |
| `/scheduled-reminder list` | Zeigt alle eingerichteten Reminder mit ID, Rolle, Kanal, Zeitplan, letzter Sendung und Nachrichten-Vorschau an. | `/scheduled-reminder list` |
| `/scheduled-reminder delete <reminder_id>` | Löscht einen wiederkehrenden Reminder. Die ID steht in `/scheduled-reminder list` und in der Antwort beim Erstellen. | `/scheduled-reminder delete reminder_id:1` |
| `/scheduled-reminder trigger-send <reminder_id>` | Sendet den ausgewählten Reminder sofort manuell. | `/scheduled-reminder trigger-send reminder_id:1` |

> [!NOTE]
> Gültige Wochentage sind `Montag`, `Dienstag`, `Mittwoch`, `Donnerstag`, `Freitag`, `Samstag`, `Sonntag`.

---

## Zeitplan

| Einstellung | Bedeutung |
|------------|-----------|
| `weekday` | Wochentag, an dem der Reminder automatisch gesendet wird. |
| `hour` | Stunde im 24h-Format von `0` bis `23`, z.B. `18` für 18:00. |
| Zeitzone | Alle Zeiten werden in `Europe/Berlin` ausgewertet. |

Der Bot prüft einmal pro Stunde, ob ein Reminder fällig ist. Automatische Reminder werden pro Kalendertag nur einmal gesendet, damit ein Bot-Neustart oder erneuter Loop-Lauf keine doppelte Nachricht erzeugt.

---

## Nachricht und Mentions

Der Bot sendet:

```text
<Rollen-Ping>
<deine Nachricht>
```

Nur die konfigurierte Rolle darf tatsächlich pingen. `@everyone`, User-Pings oder weitere Rollen-Pings innerhalb der Nachricht werden unterdrückt.

---

## Manuelles Senden

`/scheduled-reminder trigger-send` sendet den Reminder sofort, unabhängig vom konfigurierten Wochentag, der Stunde oder einer bereits automatisch gesendeten Nachricht am selben Tag.

Wenn der Zielkanal oder die Rolle nicht gefunden wird oder Discord das Senden ablehnt, markiert der Bot den Reminder nicht als gesendet.

---

## Berechtigungen

| | Abteilungsleiter | Staff | Andere Mitglieder |
|-|:---:|:---:|:---:|
| Reminder erstellen | ✅ | ✅ | ❌ |
| Reminder auflisten | ✅ | ✅ | ❌ |
| Reminder löschen | ✅ | ✅ | ❌ |
| Reminder manuell senden | ✅ | ✅ | ❌ |
