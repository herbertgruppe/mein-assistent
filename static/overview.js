/* Übersicht über den Protokoll-Workflow.
   Holt /api/protocols/overview und füllt die drei Reiter. */

(function () {
    "use strict";

    var SEHR_ALT = 14;   // Tage — ab hier rot
    var ALT = 7;         // Tage — ab hier orange

    function el(id) { return document.getElementById(id); }

    function text(inhalt) { return document.createTextNode(inhalt); }

    function node(tag, klasse, inhalt) {
        var n = document.createElement(tag);
        if (klasse) { n.className = klasse; }
        if (inhalt !== undefined && inhalt !== null) { n.appendChild(text(String(inhalt))); }
        return n;
    }

    function marke(klasse, beschriftung) {
        return node("span", "hg-marke " + klasse, beschriftung);
    }

    function altersKlasse(tage) {
        if (tage === null || tage === undefined) { return ""; }
        if (tage >= SEHR_ALT) { return " ist-sehr-alt"; }
        if (tage >= ALT) { return " ist-alt"; }
        return "";
    }

    var SPRECHER_WAHL = [
        { wert: "geprueft",     text: "geprüft",        titel: "Sprecher in Plaud geprüft" },
        { wert: "nicht_noetig", text: "nicht nötig",    titel: "Keine Sprechererkennung nötig" },
        { wert: "teilweise",    text: "teilweise",      titel: "Nur teilweise zugeordnet" },
        { wert: "offen",        text: "offen",          titel: "Noch nicht geprüft" }
    ];

    /* Der Vermerk sitzt bewusst in der Liste, nicht auf der Einzelseite:
       Sven prüft die Sprecher in Plaud und will den Haken setzen, ohne den
       Vorgang zu öffnen. Plaud selbst bietet dafür kein Feld. */
    function sprecherSchalter(e) {
        var box = node("div", "hg-sprecher" + (e.sprecher_status === "offen" ? " ist-offen" : ""));
        SPRECHER_WAHL.forEach(function (w) {
            var b = document.createElement("button");
            b.type = "button";
            b.className = "hg-sprecher-knopf" +
                (e.sprecher_status === w.wert ? " ist-gewaehlt" : "");
            b.title = w.titel;
            b.dataset.id = e.id;
            b.dataset.wert = w.wert;
            b.appendChild(text(w.text));
            box.appendChild(b);
        });
        return box;
    }

    function sprecherSetzen(knopf) {
        var id = knopf.dataset.id;
        var wert = knopf.dataset.wert;
        var gruppe = knopf.parentNode;
        gruppe.classList.add("ist-sendet");

        fetch("/api/protocols/" + encodeURIComponent(id) + "/sprecher-status", {
            method: "PATCH",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: wert })
        })
            .then(function (r) {
                if (!r.ok) { throw new Error("HTTP " + r.status); }
                return r.json();
            })
            .then(function () {
                var knoepfe = gruppe.querySelectorAll(".hg-sprecher-knopf");
                for (var i = 0; i < knoepfe.length; i++) {
                    knoepfe[i].classList.toggle("ist-gewaehlt", knoepfe[i].dataset.wert === wert);
                }
                gruppe.classList.toggle("ist-offen", wert === "offen");
            })
            .catch(function (err) { fehler("Vermerk nicht gespeichert: " + err.message); })
            .then(function () { gruppe.classList.remove("ist-sendet"); });
    }

    /* Der Eintrag ist ein <div> mit einem <a> darin, nicht umgekehrt: Der
       Prüfvermerk braucht Schaltflächen, und die lösen in einem Link die
       Navigation aus. Der Hauptteil bleibt ein echtes <a>, damit Mittelklick
       und "in neuem Tab öffnen" weiter funktionieren. */
    function eintrag(e, zeigeAlter, reiter) {
        var wrap = node("div", "hg-eintrag" + (zeigeAlter ? altersKlasse(e.alter_tage) : ""));

        var a = document.createElement("a");
        a.className = "hg-eintrag-link";
        // Der Hinweis sorgt dafuer, dass der Vorgang nach dem Absenden hierher
        // zurueckkehrt — beim Abarbeiten mehrerer Aufnahmen am Stueck ist die
        // Sackgasse danach sonst das groesste Aergernis.
        //
        // Der Reiter kommt mit, sonst landet man nach dem Freigeben wieder bei
        // "Zuordnen" und braucht jedes Mal einen Klick zurueck.
        a.href = e.link + (e.link.indexOf("?") === -1 ? "?" : "&") +
            "weiter=uebersicht" + (reiter ? "&reiter=" + reiter : "");

        var haupt = node("div", "hg-eintrag-haupt");
        var titel = node("div", "hg-eintrag-titel");

        if (e.ohne_namen) {
            titel.appendChild(marke("hg-marke-warn", "ohne Titel"));
        }
        if (e.abgelaufen) {
            titel.appendChild(marke("hg-marke-neutral", "Link abgelaufen"));
        }
        if (e.status === "assigned") {
            titel.appendChild(marke("hg-marke-info", "bei Mara"));
        }
        titel.appendChild(text(e.titel));
        haupt.appendChild(titel);

        var meta = [];
        if (e.datum) { meta.push(e.datum); }
        if (e.dauer) { meta.push(e.dauer); }
        if (e.termin && e.termin !== e.titel) { meta.push(e.termin); }
        if (e.erinnerungen > 0) {
            meta.push(e.erinnerungen + (e.erinnerungen === 1 ? " Erinnerung" : " Erinnerungen"));
        }
        if (e.hat_transkript) { meta.push("Transkript gesichert"); }
        if (meta.length) {
            haupt.appendChild(node("div", "hg-eintrag-meta", meta.join(" · ")));
        }
        a.appendChild(haupt);

        if (zeigeAlter && e.alter_tage !== null && e.alter_tage !== undefined) {
            var rechts = node("div", "hg-eintrag-alter");
            rechts.appendChild(node("strong", null, e.alter_tage));
            rechts.appendChild(text(e.alter_tage === 1 ? "Tag" : "Tage"));
            a.appendChild(rechts);
        }
        wrap.appendChild(a);

        // Der Vermerk nur dort, wo er etwas bewirkt: Ist das Transkript
        // einmal gezogen, kommt jede Sprecherkorrektur zu spaet.
        if (e.status === "pending_assignment") {
            wrap.appendChild(sprecherSchalter(e));
        }
        return wrap;
    }

    function fertigEintrag(e) {
        var wrap = node("div", "hg-eintrag");
        var haupt = node("div", "hg-eintrag-haupt");
        haupt.appendChild(node("div", "hg-eintrag-titel", e.titel));
        if (e.datum) { haupt.appendChild(node("div", "hg-eintrag-meta", e.datum)); }
        wrap.appendChild(haupt);
        if (e.asana_url) {
            var a = document.createElement("a");
            a.className = "hg-eintrag-alter";
            a.href = e.asana_url;
            a.target = "_blank";
            a.rel = "noopener";
            a.appendChild(text("Asana"));
            wrap.appendChild(a);
        }
        return wrap;
    }

    function fuellen(ziel, eintraege, bauer, leerText) {
        var box = el(ziel);
        box.textContent = "";
        if (!eintraege.length) {
            box.appendChild(node("div", "hg-leer", leerText));
            return;
        }
        eintraege.forEach(function (e) { box.appendChild(bauer(e)); });
    }

    function reiterZahl(id, wert) {
        var n = el(id);
        if (!n) { return; }
        n.textContent = wert > 0 ? String(wert) : "";
        n.hidden = wert === 0;
    }

    // "mitschreiben" ist ein statischer Reiter ohne Daten — er erklaert,
    // wie die Live-Mitschrift auf dem eigenen Rechner gestartet wird.
    var REITER = ["zuordnung", "freigabe", "fertig", "mitschreiben"];

    function reiterWechsel(ziel, merken) {
        if (REITER.indexOf(ziel) === -1) { return; }
        var tabs = document.querySelectorAll(".hg-tab");
        var i;
        for (i = 0; i < tabs.length; i++) {
            tabs[i].classList.toggle("is-aktiv", tabs[i].dataset.ziel === ziel);
        }
        var panels = document.querySelectorAll(".hg-panel");
        for (i = 0; i < panels.length; i++) {
            panels[i].classList.toggle("is-aktiv", panels[i].id === "panel-" + ziel);
        }
        // Im Adressfeld festhalten, damit ein Neuladen hier bleibt.
        // replaceState statt pushState: Das Umschalten soll den Zurück-Knopf
        // nicht mit Zwischenschritten fuellen.
        if (merken !== false && window.history && window.history.replaceState) {
            window.history.replaceState(null, "", "#" + ziel);
        }
    }

    /**
     * Welcher Reiter soll offen sein?
     *
     * Nach dem Absenden kehrt der Vorgang mit `?reiter=…` hierher zurück —
     * sonst landet man beim Freigeben jedes Mal wieder bei "Zuordnen" und
     * braucht einen Klick zurück. Bei 41 Entwürfen sind das 41 Klicks.
     */
    function gewuenschterReiter() {
        var ausUrl = new URLSearchParams(window.location.search).get("reiter");
        if (REITER.indexOf(ausUrl) !== -1) { return ausUrl; }
        var ausHash = (window.location.hash || "").replace("#", "");
        if (REITER.indexOf(ausHash) !== -1) { return ausHash; }
        return null;
    }

    function fehler(meldung) {
        var box = el("fehler");
        box.textContent = meldung;
        box.hidden = false;
    }

    function laden() {
        fetch("/api/protocols/overview", { credentials: "same-origin" })
            .then(function (r) {
                if (r.status === 401 || r.status === 403) {
                    throw new Error("Nicht angemeldet. Bitte die Seite neu laden.");
                }
                if (!r.ok) { throw new Error("Daten konnten nicht geladen werden (HTTP " + r.status + ")."); }
                return r.json();
            })
            .then(function (d) {
                var k = d.kennzahlen || {};
                el("kpi-offen").textContent = k.offen;
                el("kpi-review").textContent = k.im_review;
                el("kpi-alt").textContent = k.aeltestes_tage;
                el("kpis").hidden = false;

                if (k.sprecher_offen !== undefined) {
                    el("kpi-sprecher").textContent = k.sprecher_offen;
                }

                if (k.ohne_namen > 0) {
                    var h = el("hinweis-ohne-namen");
                    h.textContent = k.ohne_namen === 1
                        ? "Eine Aufnahme trägt in Plaud keinen sprechenden Titel — sie ist nur über das Datum zuzuordnen."
                        : k.ohne_namen + " Aufnahmen tragen in Plaud keinen sprechenden Titel — sie sind nur über das Datum zuzuordnen.";
                    h.hidden = false;
                }

                reiterZahl("tab-zahl-zuordnung", (d.zuordnung || []).length);
                reiterZahl("tab-zahl-freigabe", (d.freigabe || []).length);

                fuellen("liste-zuordnung", d.zuordnung || [],
                    function (e) { return eintrag(e, true, "zuordnung"); },
                    "Nichts offen — alle Aufnahmen sind zugeordnet.");
                fuellen("liste-freigabe", d.freigabe || [],
                    function (e) { return eintrag(e, true, "freigabe"); },
                    "Kein Entwurf wartet auf Freigabe.");
                fuellen("liste-fertig", d.fertig || [], fertigEintrag,
                    "Noch kein Protokoll fertiggestellt.");

                // Erst nach dem Befüllen umschalten — vorher sind die Listen
                // leer und der Wechsel sähe wie ein leerer Reiter aus.
                var ziel = gewuenschterReiter();
                if (ziel) {
                    reiterWechsel(ziel);
                } else if (!(d.zuordnung || []).length && (d.freigabe || []).length) {
                    // Nichts mehr zuzuordnen, aber Entwürfe offen: dann ist
                    // der erste Reiter nur eine leere Seite.
                    reiterWechsel("freigabe");
                }
            })
            .catch(function (e) { fehler(e.message); });
    }

    document.addEventListener("click", function (ev) {
        if (!ev.target.closest) { return; }
        var tab = ev.target.closest(".hg-tab");
        if (tab && tab.dataset.ziel) { reiterWechsel(tab.dataset.ziel); return; }

        var knopf = ev.target.closest(".hg-sprecher-knopf");
        if (knopf) {
            ev.preventDefault();
            sprecherSetzen(knopf);
        }
    });

    laden();
}());
