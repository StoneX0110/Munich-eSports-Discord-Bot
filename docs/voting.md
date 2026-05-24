# 🗳️ Abstimmungs-Tool

Anonyme Wahlen & Abstimmungen direkt in Discord. **Abteilungsleiter** verwalten Abstimmungen, alle Vereinsmitglieder können teilnehmen.

## Ablauf

`/session start` → `/session delegate` → `/vote start` → `/vote close` → `/session end`

---

## Befehle (für Abteilungsleiter)

| Befehl | Beschreibung | Beispiel |
|--------|-------------|---------|
| `/session start [department]` | Neue Wahlsitzung erstellen. Nur Mitglieder, die der Abteilung `department` angehören, können abstimmen. Ohne `department` können alle Vereinsmitglieder abstimmen. | `/session start department:Counter Strike` |
| `/session delegate <session_id> <user> <count>` | Delegiertenstimmen zuweisen. Gesamtstimmen = 1 + delegierte. Mit `count:0` entfernen. | `/session delegate session_id:1 user:@Max count:3` → Max hat 4 Stimmen |
| `/vote start <session_id> <title> <options>` | Abstimmung starten. Optionen kommagetrennt, min. 2, max. 25. | `/vote start session_id:1 title:Wahl 1. AL options:Alice, Bob, Enthaltung` |
| `/vote close <vote_id>` | Abstimmung schließen & Ergebnis anzeigen. | `/vote close vote_id:1` |
| `/session end <session_id>` | Alle offenen Abstimmungen der Sitzung schließen & Sitzung beenden. | `/session end session_id:1` |

> [!NOTE]
> Die Sitzungs-ID und Vote-ID werden jeweils in der Bot-Antwort angezeigt.

---

## Abstimmen

1. Auf **🗳️ Abstimmen** klicken
2. Option auswählen
3. Bei mehreren Stimmen: Anzahl aus zweitem Dropdown wählen
4. Bestätigung erscheint – ggf. wiederholen bis alle Stimmen vergeben

> Die Abstimmung ist **anonym** – niemand sieht, wofür du gestimmt hast.

---

## Berechtigungen

| | Abteilungsleiter | Mitglied | 
|-|:---:|:---:|
| Sitzungen & Abstimmungen verwalten | ✅ | ❌ | 
| Abstimmen | ✅ | ✅ (Abteilungszugehörigkeit wird verifiziert) | 
