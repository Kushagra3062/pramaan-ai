"use client";

import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { PageTitle } from "@/components/app/ProductPage";
import { EmptyCard, ErrorCard, StatusPill, formatDate } from "@/components/workspace/ui";
import { approveAction, listActions, proposeAction, rejectAction, type ActionItem } from "@/lib/api/actions";

type View = "" | "PROPOSED" | "APPROVED" | "REJECTED";

function ActionCard({ item, workspaceId, onChanged }: Readonly<{ item: ActionItem; workspaceId: string; onChanged: () => void }>) {
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mutable = item.status === "PROPOSED";
  const act = async (action: "approve" | "reject") => {
    if (busy) return;
    setBusy(true);
    setError("");
    const result = action === "approve" ? await approveAction(workspaceId, item.id, rationale.trim()) : await rejectAction(workspaceId, item.id, rationale.trim());
    setBusy(false);
    if (!result.ok) return setError(result.message);
    onChanged();
  };
  return (
    <div className="x-item">
      <div className="x-item-head">
        <StatusPill value={item.status} />
        <StatusPill value={item.priority} />
        <span className="x-muted x-note">{formatDate(item.created_at)}</span>
      </div>
      <p className="x-note">{item.description}</p>
      <dl className="x-meta">
        <div><dt>Owner</dt><dd>{item.proposed_owner ?? "—"}</dd></div>
        <div><dt>Due</dt><dd>{formatDate(item.due_date)}</dd></div>
        <div><dt>Source</dt><dd>{item.related_conflict_id ? `conflict ${item.related_conflict_id.slice(0, 8)}` : item.related_analysis_id ? `analysis ${item.related_analysis_id.slice(0, 8)}` : "manual draft"}</dd></div>
        <div><dt>Decision</dt><dd>{item.decision_rationale ?? (item.status === "PROPOSED" ? "awaiting human review" : "—")}</dd></div>
      </dl>
      {mutable ? (
        <div className="w-resolve">
          <label className="w-field">Decision rationale (optional)<input type="text" value={rationale} maxLength={2000} onChange={(event) => setRationale(event.target.value)} placeholder="Why approve or reject…" /></label>
          <div className="x-actions">
            <button type="button" className="primary-button x-small-button" disabled={busy} onClick={() => void act("approve")}>Approve</button>
            <button type="button" className="secondary-button x-small-button" disabled={busy} onClick={() => void act("reject")}>Reject</button>
          </div>
          {error ? <p className="x-banner x-banner-err">{error}</p> : null}
        </div>
      ) : (
        <p className="x-note x-muted">Decided {formatDate(item.approved_at ?? item.rejected_at)}. No external execution in this release.</p>
      )}
    </div>
  );
}

export function ActionsView({ workspaceId }: Readonly<{ workspaceId: string }>) {
  const search = useSearchParams();
  const [view, setView] = useState<View>("");
  const [items, setItems] = useState<ActionItem[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [description, setDescription] = useState("");
  const [owner, setOwner] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [priority, setPriority] = useState("MEDIUM");
  const [idempotencyKey, setIdempotencyKey] = useState(() => (typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now())));
  const [creating, setCreating] = useState(false);
  const [formError, setFormError] = useState("");
  const [formOk, setFormOk] = useState("");

  useEffect(() => {
    const draft = search.get("draft");
    if (draft) setDescription(draft);
  }, [search]);

  useEffect(() => {
    let cancelled = false;
    setError("");
    void listActions(workspaceId, view, 30).then((result) => {
      if (cancelled) return;
      if (!result.ok) return setError(result.message);
      setItems(result.data.items);
      setTotal(result.data.total);
    });
    return () => { cancelled = true; };
  }, [workspaceId, view, attempt]);

  const submit = async () => {
    if (!description.trim() || creating) return;
    setCreating(true);
    setFormError("");
    setFormOk("");
    const result = await proposeAction(workspaceId, {
      description: description.trim(),
      priority,
      proposed_owner: owner.trim() || undefined,
      due_date: dueDate ? new Date(dueDate).toISOString() : undefined,
      idempotency_key: idempotencyKey,
    });
    setCreating(false);
    if (!result.ok) return setFormError(result.message);
    setFormOk("Draft saved. Double submits with the same key return this same draft.");
    setDescription("");
    setOwner("");
    setDueDate("");
    setIdempotencyKey(typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now()));
    setAttempt((n) => n + 1);
  };

  return (
    <>
      <PageTitle eyebrow="Action Center" title="Actions">
        <p>Draft proposals, human approval and rejection. Approval is role-checked and audit-logged — never automatic.</p>
      </PageTitle>
      <div className="w-tabs" role="tablist" aria-label="Action views">
        {([["", "All"], ["PROPOSED", "Draft proposals"], ["APPROVED", "Approved"], ["REJECTED", "Rejected"]] as [View, string][]).map(([value, label]) => (
          <button key={label} role="tab" aria-selected={view === value} type="button" className={view === value ? "w-tab active" : "w-tab"} onClick={() => setView(value)}>{label}</button>
        ))}
      </div>
      <section className="x-card">
        <div className="x-card-head"><h3>New draft</h3><span className="x-muted x-note">key {idempotencyKey.slice(0, 8)}…</span></div>
        <label className="w-field">Description<textarea className="w-textarea" rows={3} maxLength={4000} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What should happen, with source reference…" /></label>
        <div className="w-form-row">
          <label className="w-field">Owner (optional)<input type="text" value={owner} maxLength={255} onChange={(event) => setOwner(event.target.value)} placeholder="Team or person" /></label>
          <label className="w-field">Due date (optional)<input type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} /></label>
          <label className="w-field">Priority
            <select value={priority} onChange={(event) => setPriority(event.target.value)}>
              <option value="LOW">Low</option><option value="MEDIUM">Medium</option><option value="HIGH">High</option><option value="URGENT">Urgent</option>
            </select>
          </label>
        </div>
        <div className="x-actions">
          <button type="button" className="primary-button" disabled={!description.trim() || creating} onClick={() => void submit()}>{creating ? "Saving…" : "Save draft"}</button>
          <button type="button" className="secondary-button" onClick={() => setIdempotencyKey(typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : String(Date.now()))}>New idempotency key</button>
        </div>
        {formError ? <p className="x-banner x-banner-err">{formError}</p> : null}
        {formOk ? <p className="x-banner x-banner-ok">{formOk}</p> : null}
      </section>
      {error ? <ErrorCard title="Actions unavailable" message={error} onRetry={() => setAttempt((n) => n + 1)} /> : null}
      {!error && items.length === 0 ? <EmptyCard title="Nothing in this view" detail="Drafts created above appear here for human decision." /> : null}
      {!error && items.length > 0 ? <p className="x-note x-muted">{total} action(s) in this view.</p> : null}
      <div className="x-list">{items.map((item) => <ActionCard key={item.id} item={item} workspaceId={workspaceId} onChanged={() => setAttempt((n) => n + 1)} />)}</div>
    </>
  );
}
