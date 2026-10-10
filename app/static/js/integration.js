/* Existing pages use the same authenticated JSON API as other consumers. */
(async function () {
  "use strict";
  const data = JSON.parse(document.getElementById("setmaker-page-data").textContent);
  const main = document.querySelector("main");
  const notice = document.createElement("p");
  notice.id = "integration-notice";
  notice.setAttribute("role", "status");
  main.prepend(notice);
  let csrf;
  async function api(path, method = "GET", body) {
    const headers = {Accept: "application/json"};
    if (method !== "GET") {
      if (!csrf) {
        const response = await fetch("/csrf-token", {credentials: "same-origin"});
        if (!response.ok) throw new Error("Could not obtain CSRF token.");
        csrf = (await response.json()).csrf_token;
      }
      headers["X-CSRFToken"] = csrf;
      if (body !== undefined) headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, {method, headers, credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body)});
    const result = response.status === 204 ? {} : await response.json();
    if (!response.ok) {
      const error = new Error(result.message || "Request failed.");
      error.code = result.error;
      throw error;
    }
    return result;
  }
  const showError = error => { notice.textContent = error.message; };
  function roundFormat(value) {
    if (["Mixed Doubles", "mixedDoubles"].includes(value)) return "mixed";
    if (["Open Play (Any Gender)", "openPlay"].includes(value)) return "open";
    throw new Error("That round format is not available yet.");
  }
  function element(tag, text, parent) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (parent) parent.append(node);
    return node;
  }
  function button(text, parent, action) {
    const node = element("button", text, parent);
    node.type = "button";
    node.className = "btn";
    node.onclick = async () => {
      node.disabled = true;
      try { await action(); } catch (error) { showError(error); }
      finally { node.disabled = false; }
    };
    return node;
  }
  function field(form, name, label, value, type = "text") {
    let input = form.elements?.namedItem(name) || form.querySelector(`[name="${name}"]`);
    if (!input) {
      const row = element("div", undefined, form);
      row.className = "field";
      const caption = element("label", label, row);
      input = element("input", undefined, row);
      input.id = name;
      caption.htmlFor = name;
      input.name = name;
      input.type = type;
      input.className = "input";
    }
    input.value = value ?? "";
    return input;
  }
  function choices(container, items, name, selected = []) {
    container.replaceChildren();
    for (const item of items) {
      const label = element("label", undefined, container);
      label.className = "choice";
      const input = element("input", undefined, label);
      input.type = "checkbox";
      input.name = name;
      input.value = item.id;
      input.checked = selected.includes(item.id);
      label.append(document.createTextNode(item.name || `Court ${item.number} (${item.court_type})`));
    }
  }
  function attachForm(form, save) {
    form.onsubmit = async event => {
      event.preventDefault();
      const submit = form.querySelector('[type="submit"]');
      submit.disabled = true;
      notice.textContent = "";
      try { await save(new FormData(form)); }
      catch (error) { showError(error); }
      finally { submit.disabled = false; }
    };
  }
  try {
    if (data.page === "players" && !data.can_manage) {
      document.querySelectorAll(".delete-form").forEach(form => form.remove());
    }
    if (data.page === "player-form") {
      const form = document.querySelector("form");
      if (!data.can_manage_skill) {
        form.elements.rating.disabled = true;
        form.elements.rating.required = false;
      }
      const confirm = element("label", undefined, form);
      const check = element("input", undefined, confirm);
      check.type = "checkbox";
      check.name = "confirm_duplicate";
      confirm.append(document.createTextNode("Confirm this is a different person if a duplicate warning appears."));
      attachForm(form, async values => {
        const payload = {name: values.get("name"), gender: values.get("gender") === "Male" ? "M" : "F",
          notes: values.get("notes"), court_preference: values.get("court_preference"),
          confirm_duplicate: values.has("confirm_duplicate")};
        if (data.can_manage_skill) payload.skill = Number(values.get("rating"));
        const id = data.player?.id;
        await api(id ? `/api/players/${id}` : "/api/players", id ? "PATCH" : "POST", payload);
        location.assign("/players");
      });
    }
    if (data.page === "courts") {
      document.querySelectorAll("[data-court-toggle]").forEach(toggle => {
        toggle.onchange = async () => {
          toggle.disabled = true;
          try {
            await api(`/api/courts/${toggle.dataset.courtToggle}`, "PATCH", {active: toggle.checked});
            location.reload();
          } catch (error) { toggle.checked = !toggle.checked; showError(error); }
          finally { toggle.disabled = false; }
        };
      });
    }
    if (data.page === "court-form") {
      const form = document.querySelector("form");
      if (data.court) {
        form.elements["court-name"].value = data.court.number;
        const surface = data.court.court_type.charAt(0).toUpperCase() + data.court.court_type.slice(1).toLowerCase();
        if (!Array.from(form.elements.surface.options).some(option => option.value === surface)) {
          const option = element("option", data.court.court_type, form.elements.surface);
          option.value = surface;
        }
        form.elements.surface.value = surface;
        form.elements.cover.value = data.court.cover || "";
        form.elements.priority.value = data.court.priority;
        form.querySelector('[type="checkbox"]').checked = data.court.active;
        form.querySelector('[type="submit"]').textContent = "Save Court";
      }
      form.elements.priority.min = "0";
      attachForm(form, async values => {
        const payload = {number: Number(values.get("court-name")), court_type: values.get("surface"),
          cover: values.get("cover"), priority: Number(values.get("priority") || 0),
          active: form.querySelector('[type="checkbox"]').checked};
        await api(data.court ? `/api/courts/${data.court.id}` : "/api/courts", data.court ? "PATCH" : "POST", payload);
        location.assign("/courts");
      });
    }
    if (data.page === "template-form" || data.page === "session-form") {
      const form = document.querySelector("form");
      const courts = (await api("/api/courts")).courts.filter(court => court.active);
      const template = data.template;
      if (data.page === "template-form") {
        form.elements["template-name"].value = template?.name || "";
        form.elements.format.value = template?.round_format === "mixed" ? "Mixed Doubles" : "Open Play (Any Gender)";
        field(form, "rounds", "Number of Rounds", template?.number_of_rounds || 6, "number");
        field(form, "round-length", "Round Length (minutes)", template?.round_duration_minutes || 30, "number");
        field(form, "start_time", "Start Time", template?.start_time || "13:00", "time");
        field(form, "session_duration_minutes", "Session Duration (minutes)", template?.session_duration_minutes || 180, "number");
        field(form, "tea_break_after_round", "Tea Break After Round (optional)", template?.tea_break_after_round, "number");
        const panel = document.querySelector(".template-priority-list .court-list");
        choices(panel, courts, "court_ids", template?.court_ids || courts.map(court => court.id));
        attachForm(form, async values => {
          const useDefaults = document.getElementById("use-default-priority").checked;
          const payload = {name: values.get("template-name"), number_of_rounds: Number(values.get("rounds")),
            round_duration_minutes: Number(values.get("round-length")), start_time: values.get("start_time"),
            session_duration_minutes: Number(values.get("session_duration_minutes")),
            round_format: roundFormat(values.get("format")),
            tea_break_after_round: values.get("tea_break_after_round") ? Number(values.get("tea_break_after_round")) : null,
            court_ids: useDefaults ? courts.map(court => court.id) : values.getAll("court_ids")};
          await api(template ? `/api/session-templates/${template.id}` : "/api/session-templates", template ? "PATCH" : "POST", payload);
          location.assign("/templates");
        });
      } else {
        const players = (await api("/api/players")).players.filter(player => player.skill_ready);
        form.elements.sessionName.value = template?.name || "";
        form.elements.startTime.value = template?.start_time || "13:00";
        const roundsInput = document.getElementById("roundCount");

        roundsInput.value = template?.number_of_rounds || 6;
        const duration = form.elements.roundDuration;
        for (const minutes of [20, 25, 30, 45, 60, template?.round_duration_minutes].filter(Boolean)) {
          const option = element("option", `${minutes} minutes`, duration); option.value = minutes;
        }
        duration.value = template?.round_duration_minutes || 30;
        field(form, "session_duration_minutes", "Session Duration (minutes)", template?.session_duration_minutes || 180, "number");
        field(form, "tea_break_after_round", "Tea Break After Round (optional)", template?.tea_break_after_round, "number");
        form.elements.matchFormat.value = template?.round_format === "mixed" ? "mixedDoubles" : "openPlay";
        form.elements.allowRelaxation.checked = !template?.strict_format;
        choices(document.getElementById("courtOptions"), courts, "court_ids", template?.court_ids || courts.map(court => court.id));
        const playerChoices = element("section", undefined, form);
        element("h2", "Attending Players", playerChoices);
        const list = element("div", undefined, playerChoices);
        choices(list, players, "player_ids");
        form.elements.venue.closest(".field").hidden = true;
        document.querySelector(".segmented").closest(".field").hidden = true;
        attachForm(form, async values => {
          const payload = {name: values.get("sessionName"), starts_at: `${values.get("sessionDate")}T${values.get("startTime")}`,
            number_of_rounds: Number(values.get("number_of_rounds")), round_duration_minutes: Number(values.get("roundDuration")),
            session_duration_minutes: Number(values.get("session_duration_minutes")),
            round_format: roundFormat(values.get("matchFormat")),
            strict_format: !values.has("allowRelaxation"), court_ids: values.getAll("court_ids"), player_ids: values.getAll("player_ids"),
            tea_break_after_round: values.get("tea_break_after_round") ? Number(values.get("tea_break_after_round")) : null};
          if (template) payload.template_id = template.id;
          const session = (await api("/api/sessions", "POST", payload)).session;
          // Persist configuration first; generation is retryable on the live page.
          try { await api(`/api/sessions/${session.id}/rounds/generate`, "POST"); }
          catch (error) { location.assign(`/sessions/${session.id}?generation_failed=1`); return; }
          location.assign(`/sessions/${session.id}`);
        });
      }
    }
    if (data.page === "dashboard") {
      const [sessions, players, courts] = await Promise.all([api("/api/sessions"), api("/api/players"), api("/api/courts")]);
      document.getElementById("registeredPlayers").textContent = players.players.length;
      document.getElementById("availableCourts").textContent = courts.courts.filter(court => court.active).length;
      const active = sessions.sessions.filter(session => ["draft", "active"].includes(session.status));
      document.getElementById("activeSessions").textContent = active.length;
      document.getElementById("nextRound").textContent = "Open a session";
      const list = document.getElementById("sessionList");
      for (const session of active) {
        const link = element("a", `${session.name} · ${session.starts_at} · ${session.status}`, list);
        link.href = `/sessions/${session.id}`; link.className = "card";
      }
      if (!active.length) element("p", "No active sessions yet.", list);
      document.getElementById("recentActivity").textContent = "Open a session to view its saved rounds.";
    }
    if (data.page === "active-session") {
      const session = (await api(`/api/sessions/${data.session.id}`)).session;
      const players = (await api("/api/players?include_inactive=true")).players;
      const courts = (await api("/api/courts")).courts;
      const byId = new Map(players.map(player => [player.id, player]));
      const courtById = new Map(courts.map(court => [court.id, court]));
      const actions = document.querySelector(".bottom-actions");
      actions.replaceChildren();
      // Remove unimplemented export/demo controls rather than claiming they work.
      document.querySelector(".page-topline > button")?.remove();
      const round = session.rounds.at(-1);
      document.getElementById("sessionTitle").textContent = session.name;
      document.getElementById("sessionTimer").textContent = session.status;
      document.getElementById("playerCount").textContent = session.participants.length;
      document.getElementById("courtCount").textContent = session.court_ids.length;
      document.getElementById("currentRound").textContent = round?.number || "Not generated";
      const selector = document.querySelector(".rounds");
      selector.replaceChildren();
      let selectedRound = round;
      let final;
      function draw(current) {
        selectedRound = current;
        final = current ? structuredClone(current.final) : {assignments: [], sit_out_player_ids: []};
        const matches = document.getElementById("matchGrid");
        const sitting = document.getElementById("sitoutList");
        matches.replaceChildren(); sitting.replaceChildren();
        document.getElementById("currentRound").textContent = current?.number || "Not generated";
        document.getElementById("matchCount").textContent = final.assignments.length;
        document.getElementById("sitoutCount").textContent = final.sit_out_player_ids.length;
        const eligible = current?.eligibility.filter(item => item.status === "eligible").map(item => item.player_id) || [];
        function playerSelect(parent, id, change) {
          const select = element("select", undefined, parent);
          select.className = "select";
          for (const playerId of eligible) {
            const option = element("option", byId.get(playerId)?.name || playerId, select);
            option.value = playerId;
          }
          select.value = id;
          select.disabled = current.status !== "generated";
          select.onchange = () => change(select.value);
        }
        for (const assignment of final.assignments) {
          const card = element("div", undefined, matches); card.className = "card";
          element("h3", `Court ${courtById.get(assignment.court_id)?.number || assignment.court_id}`, card);
          for (const team of ["team_a_player_ids", "team_b_player_ids"]) {
            element("p", team === "team_a_player_ids" ? "Team A" : "Team B", card);
            assignment[team].forEach((id, index) => playerSelect(card, id, value => {assignment[team][index] = value;}));
          }
        }
        final.sit_out_player_ids.forEach((id, index) => playerSelect(sitting, id, value => {final.sit_out_player_ids[index] = value;}));
        document.getElementById("matchExplanation").textContent = current?.status === "completed" ? "Completed round — locked." : "Edit the player selections, then save. Duplicate players are rejected by the backend.";
      }
      for (const saved of session.rounds) button(`Round ${saved.number} (${saved.status})`, selector, () => draw(saved));
      draw(round);
      if (!round || round.status === "completed") {
        if (["draft", "active"].includes(session.status)) button("Generate Next Round", actions, async () => {
          await api(`/api/sessions/${session.id}/rounds/generate`, "POST"); location.reload();
        });
      }
      if (round?.status === "generated") {
        button("Undo Unsaved Changes", actions, () => draw(selectedRound));
        button("Save Changes", actions, async () => {
          if (selectedRound?.status !== "generated") throw new Error("Completed rounds are locked.");
          await api(`/api/rounds/${selectedRound.id}/final`, "PUT", {...final, expected_revision: selectedRound.override_revision});
          location.reload();
        });
        button("Complete Round", actions, async () => {
          if (selectedRound.id !== round.id) throw new Error("Select the current round first.");
          if (JSON.stringify(final) !== JSON.stringify(selectedRound.final)) throw new Error("Save or undo your changes before completing the round.");
          await api(`/api/rounds/${round.id}/complete`, "POST"); location.reload();
        });
      }
      if (["draft", "active"].includes(session.status)) {
        const late = element("select", undefined, actions); late.id = "late-player";
        const joined = new Set(session.participants.map(item => item.player_id));
        for (const player of players.filter(player => player.active && !joined.has(player.id))) {
          const option = element("option", player.name, late); option.value = player.id;
        }
        button("Add Late Player", actions, async () => {
          if (!late.value) throw new Error("Select a player who has not joined yet.");
          await api(`/api/sessions/${session.id}/players`, "POST", {player_id: late.value}); location.reload();
        });
        const leave = element("select", undefined, actions); leave.id = "unavailable-player";
        for (const participant of session.participants.filter(item => !item.unavailable_from_round)) {
          const option = element("option", byId.get(participant.player_id)?.name || participant.player_id, leave);
          option.value = participant.player_id;
        }
        button("Unavailable From Next Round", actions, async () => {
          if (!leave.value) throw new Error("Select a participant.");
          await api(`/api/sessions/${session.id}/players/${leave.value}`, "PATCH", {reason: "Unavailable for remainder of session"}); location.reload();
        });
      }
      if (new URLSearchParams(location.search).has("generation_failed")) notice.textContent = "Session saved. Generation failed; retry Generate Next Round to see the error.";
    }
  } catch (error) { showError(error); }
})();
