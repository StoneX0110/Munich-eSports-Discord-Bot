# 📅 Wiederkehrende Termin-Umfragen

Automatische Verfügbarkeitsumfragen für Trainings, Scrims oder andere regelmäßige Termine direkt in Discord. **Abteilungsleiter oder Staff** richten die Umfragen ein, die ausgewählte Rolle trägt ihre Verfügbarkeit ein.

## Ablauf

`/scheduled-poll create` → automatische Wochenumfrage → Mitglieder stimmen ab → optionaler Reminder → nächste Woche wird automatisch neu gepostet

---

## Befehle (für Abteilungsleiter oder Staff)

| Befehl | Beschreibung | Beispiel |
|--------|-------------|---------|
| `/scheduled-poll create <role> <postet_am>` | Erstellt eine wöchentliche Umfrage für `role`. Die Umfrage wird in dem Kanal gepostet, in dem der Befehl ausgeführt wird. | `/scheduled-poll create role:@CS Main Team postet_am:Mittwoch` würde jeden Mittwoch das CS Main Team pingen und nach Terminverfügbarkeit für die kommende Spielwoche fragen.|
| `/scheduled-poll create <role> <postet_am> <reminder_weekday> <reminder_hour>` | Erstellt eine wöchentliche Umfrage mit Reminder. `reminder_hour` ist eine Stunde im 24h-Format von `0` bis `23`. | `/scheduled-poll create role:@CS Main Team postet_am:Mittwoch reminder_weekday:Sonntag reminder_hour:18` macht das gleiche wie oben, nur dass jeden Sonntag um 18 Uhr alle aus dem CS Main Team gepingt werden, die bis dahin noch nicht abgestimmt haben.|
| `/scheduled-poll create <role> <postet_am> erster_tag_der_spielwoche:<weekday>` | Erstellt eine wöchentliche Umfrage mit frei wählbarem ersten Tag der Spielwoche. Ohne Angabe startet die Spielwoche am Montag. | `/scheduled-poll create role:@CS Main Team postet_am:Mittwoch erster_tag_der_spielwoche:Freitag` fragt die Verfügbarkeit für die nächste Spielwoche von Freitag bis Donnerstag ab. |
| `/scheduled-poll list` | Zeigt alle eingerichteten wiederkehrenden Umfragen mit ID, Rolle, Kanal, Tag an dem gepostet wird, ersten Tag der Spielwoche und Reminder an. | `/scheduled-poll list` |
| `/scheduled-poll delete <poll_id>` | Löscht eine wiederkehrende Umfrage. Die ID steht in `/scheduled-poll list` und in der Antwort beim Erstellen. | `/scheduled-poll delete poll_id:1` |
| `/scheduled-poll trigger-post <poll_id>` | Postet die ausgewählte Umfrage sofort manuell. Nützlich, wenn die Umfrage an einem späteren Tag in der Woche erstellt wird, als sie eigentlich gepostet werden soll. | `/scheduled-poll trigger-post poll_id:1` |
| `/scheduled-poll trigger-reminder <poll_id>` | Löst den Reminder für die ausgewählte aktive Umfrage sofort manuell aus. | `/scheduled-poll trigger-reminder poll_id:1` |

> [!NOTE]
> Gültige Wochentage sind `Montag`, `Dienstag`, `Mittwoch`, `Donnerstag`, `Freitag`, `Samstag`, `Sonntag`.

---

## Zeitplan

| Einstellung | Bedeutung |
|------------|-----------|
| `postet_am` | Wochentag, an dem die neue Umfrage automatisch gepostet wird. |
| Posting-Zeit | Die Umfrage wird in der 08:00-Uhr-Stunde in `Europe/Berlin` gepostet. |
| `erster_tag_der_spielwoche` | Optionaler erster Wochentag der abgefragten Spielwoche. Standard ist `Montag`. |
| Spielwoche | Die Umfrage fragt immer die Verfügbarkeit für die nächste Spielwoche ab, beginnend mit `erster_tag_der_spielwoche`. |
| `reminder_weekday` | Optionaler Wochentag, an dem Nicht-Antwortende erinnert werden. |
| `reminder_hour` | Optionale Reminder-Stunde im 24h-Format, z.B. `18` für 18:00 oder `5` für 05:00. |

Wenn kein Reminder angegeben wird, verschickt der Bot für diese Umfrage keine automatischen Reminder.

> [!IMPORTANT]
> `reminder_weekday` und `reminder_hour` müssen gemeinsam angegeben werden. Nur eins von beiden reicht nicht.

---

## Abstimmen

Die Umfrage enthält Buttons für alle Wochentage und einen Button für **Keine Zeit**.

1. Auf einen oder mehrere Wochentage klicken: `Mo`, `Di`, `Mi`, `Do`, `Fr`, `Sa`, `So`
2. Der eigene Name erscheint unter den gewählten Tagen
3. Erneut auf einen gewählten Tag klicken, um ihn wieder zu entfernen
4. **Keine Zeit** auswählen, wenn keiner der Termine passt

Regeln beim Abstimmen:

| Aktion | Ergebnis |
|--------|----------|
| Wochentag auswählen | Der Tag wird hinzugefügt. |
| Gewählten Wochentag erneut anklicken | Der Tag wird entfernt. |
| **Keine Zeit** auswählen | Alle gewählten Tage werden entfernt, der Name landet in der Keine-Zeit-Liste. |
| Nach **Keine Zeit** einen Wochentag auswählen | **Keine Zeit** wird entfernt, der Wochentag wird gesetzt. |

> Nur Mitglieder der eingerichteten Umfrage-Rolle können abstimmen.

---

## Reminder

Wenn ein Reminder konfiguriert ist, pingt der Bot zur eingestellten Zeit alle Mitglieder der Umfrage-Rolle, die noch nicht geantwortet haben.

Als geantwortet zählt:

- mindestens ein Wochentag wurde gewählt
- oder **Keine Zeit** wurde gewählt

Der Reminder enthält einen Link zur aktiven Umfrage. Pro aktiver Umfrage wird der automatische Reminder nur einmal verschickt.

---

## Wöchentliche Erneuerung

Pro Konfiguration gibt es immer nur eine aktive Umfrage.

Wenn eine neue Wochenumfrage gepostet wird:

1. Die alte aktive Umfrage wird geschlossen
2. Die Buttons der alten Nachricht werden entfernt
3. Eine neue Umfrage für die nächste Woche wird gepostet
4. Die konfigurierte Rolle wird im neuen Beitrag gepingt

Falls der Bot in derselben Woche mehrfach in der Posting-Stunde läuft oder neu startet, wird dieselbe Wochenumfrage nicht doppelt gepostet.

---

## Berechtigungen

| | Abteilungsleiter | Staff | Mitglied der Umfrage-Rolle | Andere Mitglieder |
|-|:---:|:---:|:---:|:---:|
| Wiederkehrende Umfragen erstellen | ✅ | ✅ | ❌ | ❌ |
| Wiederkehrende Umfragen auflisten | ✅ | ✅ | ❌ | ❌ |
| Wiederkehrende Umfragen löschen | ✅ | ✅ | ❌ | ❌ |
| Manuelles Posten / Reminder auslösen | ✅ | ✅ | ❌ | ❌ |
| In einer aktiven Umfrage abstimmen | ✅, wenn in der Rolle | ✅, wenn in der Rolle | ✅ | ❌ |
