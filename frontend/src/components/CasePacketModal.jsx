import React, { useState } from 'react';
import {
  ShieldAlert, AlertTriangle, FileCheck,
  Building2, User, MapPin, Layers, Activity, Scale, Send, Loader2,
} from 'lucide-react';
import {
  Dialog, DialogContent, DialogTitle, DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { Skeleton } from '@/components/ui/skeleton';
import { Textarea } from '@/components/ui/textarea';
import { toast } from 'sonner';
import { formatINR } from '@/lib/format';

const OUTCOMES = [
  { id: 'legitimate', label: 'Legitimate Work', desc: 'Valid docs & progress' },
  { id: 'data-quality issue', label: 'Data-Quality Issue', desc: 'Typo or portal error' },
  { id: 'irregularity', label: 'Potential Irregularity', desc: 'Procedural/cost flaw' },
  { id: 'confirmed fraud', label: 'Confirmed Fraud', desc: 'Fictitious or stolen' },
];

export default function CasePacketModal({
  workId,
  packet,
  isLoading = false,
  onClose,
  currentRole,
  onSubmitReview,
  isSubmittingReview
}) {
  const [outcome, setOutcome] = useState('irregularity');
  const [notes, setNotes] = useState('');

  const isPublicTier = currentRole === 'Read-Only Public Tier';

  const handleReviewSubmit = async (e) => {
    e.preventDefault();
    if (isPublicTier) return;

    const ok = await onSubmitReview(packet.work_id, outcome, notes);
    if (ok) {
      toast.success('Audit review outcome recorded successfully.');
    } else {
      toast.error('Review submission failed. Check your role and try again.');
    }
  };

  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent
        className="glass-panel max-w-4xl sm:max-w-4xl max-h-[92vh] overflow-y-auto rounded-3xl gap-0 p-0"
        style={{ zIndex: 70 }}
      >
        {(isLoading || !packet) ? (
          /* Loading skeleton while the case packet is assembled */
          <div className="p-8 space-y-5">
            <div className="flex items-center gap-3">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
              <p className="text-sm text-muted-foreground">Assembling case packet for <span className="font-mono">{workId}</span>…</p>
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3.5">
              {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-24 rounded-2xl" />)}
            </div>
            <Skeleton className="h-40 rounded-2xl" />
            <Skeleton className="h-56 rounded-2xl" />
          </div>
        ) : (
          <div className="p-6 sm:p-8 space-y-6">

            {/* Header */}
            <div className="space-y-1 border-b pb-5">
              <div className="flex flex-wrap items-center gap-2.5">
                <span className="font-mono text-sm font-bold bg-muted px-3 py-1 rounded-lg border">
                  {packet.work_id}
                </span>
                <span className={`text-xs font-semibold px-3 py-1 rounded-full border ${getTierBadge(packet.risk_tier)}`}>
                  {packet.risk_tier}
                </span>
                <Badge variant="outline" className="border-primary/30 bg-primary/5 text-primary text-xs font-bold">
                  Priority Rank #{packet.priority_rank}
                </Badge>
              </div>
              <DialogTitle asChild>
                <h2 className="text-2xl font-bold tracking-tight pt-1">Audit Case Packet</h2>
              </DialogTitle>
              <DialogDescription>
                Evidence-grounded audit review dossier generated via MoSPI Risk Engine v4.
              </DialogDescription>
            </div>

            {/* Key metrics */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3.5">
              <MetricCard label="Final Risk Score">
                <div className="text-2xl font-black mt-1 flex items-baseline gap-1">
                  {packet.final_risk_score}
                  <span className="text-xs font-normal text-muted-foreground">/ 100</span>
                </div>
                <span className="text-[10px] text-muted-foreground block mt-0.5">Statistical review index</span>
              </MetricCard>

              <MetricCard label="Sanctioned Value">
                <div className="text-lg font-bold mt-1 font-mono">{formatINR(packet.sanction_amount)}</div>
                <span className="text-[10px] text-muted-foreground block mt-0.5">Approved budget</span>
              </MetricCard>

              <MetricCard label="Fund Disbursed">
                <div className="text-lg font-bold mt-1 font-mono">{formatINR(packet.total_fund_disbursed)}</div>
                <span className="text-[10px] text-muted-foreground block mt-0.5">
                  Utilized: {Math.round(packet.utilization_ratio * 100)}%
                </span>
              </MetricCard>

              <MetricCard label="Action Directive">
                <div className="text-xs font-bold text-amber-600 mt-1.5 leading-tight">
                  {packet.recommended_action || 'Routine Monitoring'}
                </div>
                <span className="text-[10px] text-muted-foreground block mt-1">Recommended workflow</span>
              </MetricCard>
            </div>

            {/* Administrative profile */}
            <div className="p-5 rounded-2xl bg-muted/40 border space-y-3">
              <h3 className="text-xs font-bold uppercase tracking-wider text-muted-foreground">
                Administrative & Execution Profile
              </h3>

              <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-y-3 gap-x-4 text-xs">
                <Field label="Hon'ble MP:" icon={<User className="w-3.5 h-3.5 text-muted-foreground" />}>
                  {packet.mp_name || 'N/A'}
                </Field>
                <Field label="State & Constituency:" icon={<MapPin className="w-3.5 h-3.5 text-muted-foreground" />}>
                  {packet.constituency ? `${packet.constituency}, ` : ''}{packet.state}
                </Field>
                <Field label="Implementing Agency (IDA):" icon={<Building2 className="w-3.5 h-3.5 text-muted-foreground" />}>
                  {packet.ida || 'Not specified'}
                </Field>
                <Field label="Primary Vendor / Contractor:" icon={<Building2 className="w-3.5 h-3.5 text-muted-foreground" />}>
                  {packet.primary_vendor || 'Vendor not recorded in payments'}
                </Field>
                <Field label="Work Category:" icon={<Layers className="w-3.5 h-3.5 text-muted-foreground" />}>
                  {packet.work_category || 'Normal / Others'}
                </Field>
                <Field label="Work Status:" icon={<Activity className="w-3.5 h-3.5 text-muted-foreground" />}>
                  {packet.work_status || 'Under Implementation'}
                </Field>
              </div>

              <div className="pt-2 border-t text-xs">
                <span className="text-muted-foreground block">Work Description / Type:</span>
                <p className="text-foreground/90 mt-0.5 font-medium">{packet.work_type}</p>
              </div>
            </div>

            {/* Explainability dossier */}
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-bold flex items-center gap-2">
                  <ShieldAlert className="w-4 h-4 text-amber-400" />
                  Explainability Dossier — Triggered Anomaly Signals
                </h3>
                <span className="text-xs text-muted-foreground">
                  {packet.rule_flag_count} signal{packet.rule_flag_count !== 1 ? 's' : ''} detected
                </span>
              </div>

              <div className="space-y-2.5">
                {packet.causes && packet.causes.length > 0 ? (
                  packet.causes.map((cause, idx) => (
                    <div key={idx} className="p-3.5 rounded-xl bg-background/60 border border-amber-200 flex items-start gap-3">
                      <div className="w-6 h-6 rounded-lg bg-amber-500/10 flex items-center justify-center shrink-0 mt-0.5">
                        <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                      </div>
                      <div className="space-y-0.5 text-xs">
                        <span className="font-semibold text-foreground/90 block">Evidence Factor #{idx + 1}</span>
                        <p className="text-foreground/75 leading-relaxed">{cause}</p>
                      </div>
                    </div>
                  ))
                ) : (
                  <div className="p-4 rounded-xl bg-background/60 border text-xs text-muted-foreground italic">
                    No individual rule violated; prioritized based on statistical portfolio modeling.
                  </div>
                )}
              </div>

              {packet.impact_note && (
                <div className="p-3 rounded-xl bg-primary/10 border border-primary/25 text-xs text-accent-foreground flex items-center gap-2">
                  <Scale className="w-4 h-4 shrink-0 text-primary" />
                  <span>{packet.impact_note}</span>
                </div>
              )}
            </div>

            {/* Human review feedback loop */}
            <div className="p-5 rounded-2xl bg-gradient-to-br from-background via-background to-primary/10 border space-y-4">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <FileCheck className="w-5 h-5 text-primary" />
                  <h3 className="text-sm font-bold">Human Auditor Review & Decision Record</h3>
                </div>
                <span className="text-[11px] font-medium text-muted-foreground">
                  Active Role: <strong className="text-primary">{currentRole}</strong>
                </span>
              </div>

              {isPublicTier ? (
                <div className="p-3.5 rounded-xl bg-muted/60 border text-xs text-muted-foreground">
                  <span className="font-semibold text-amber-700 block mb-1">Access Restricted (Read-Only Public Tier)</span>
                  Public visitors can inspect case packets and risk scores, but cannot submit or modify official audit determinations. Switch to <strong>MoSPI Reviewer</strong> or <strong>District Auditor</strong> role to record findings.
                </div>
              ) : (
                <form onSubmit={handleReviewSubmit} className="space-y-4 text-xs">

                  <div>
                    <label className="text-muted-foreground font-medium block mb-2">
                      Select Formal Audit Determination (Ground Truth Feedback):
                    </label>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                      {OUTCOMES.map((item) => (
                        <button
                          key={item.id}
                          type="button"
                          onClick={() => setOutcome(item.id)}
                          className={`p-2.5 rounded-xl text-left border transition-all ${
                            outcome === item.id
                              ? 'bg-primary/10 border-primary text-primary shadow-sm'
                              : 'bg-background/60 text-muted-foreground hover:border-border'
                          }`}
                        >
                          <span className="font-bold text-xs block">{item.label}</span>
                          <span className="text-[10px] text-muted-foreground block mt-0.5">{item.desc}</span>
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="text-muted-foreground font-medium block mb-1.5">
                      Audit Notes / Field Observation Remarks:
                    </label>
                    <Textarea
                      rows={2}
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder="Record verification references, inspection dates, contractor verification, or physical verification findings..."
                      className="bg-background/60 text-xs"
                    />
                  </div>

                  <div className="flex items-center justify-between pt-1">
                    <span className="text-muted-foreground text-[11px]">
                      Saves determination to the audit log table.
                    </span>

                    <Button type="submit" size="sm" disabled={isSubmittingReview}>
                      {isSubmittingReview ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                      {isSubmittingReview ? 'Recording…' : 'Submit Review'}
                    </Button>
                  </div>

                </form>
              )}

              {/* Prior reviews audit trail */}
              {packet.prior_reviews && packet.prior_reviews.length > 0 && (
                <div className="pt-3 border-t space-y-2">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground block">
                    Recorded Review History ({packet.prior_reviews.length})
                  </span>
                  <div className="space-y-1.5">
                    {packet.prior_reviews.map((rev) => (
                      <div key={rev.id} className="p-2.5 rounded-xl bg-background/60 border text-xs flex items-start justify-between gap-4">
                        <div className="space-y-0.5">
                          <span className="font-semibold text-primary capitalize">{rev.outcome}</span>
                          <p className="text-muted-foreground text-[11px]">{rev.notes || 'No comments attached.'}</p>
                        </div>
                        <div className="text-right text-[10px] text-muted-foreground shrink-0">
                          <span className="block font-medium text-foreground/70">{rev.reviewer_name}</span>
                          <span>{new Date(rev.created_at).toLocaleDateString()}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function getTierBadge(tier) {
  if (tier?.includes('High')) return 'bg-red-50 text-red-700 border-red-200';
  if (tier?.includes('Medium')) return 'bg-amber-50 text-amber-700 border-amber-200';
  return 'bg-emerald-50 text-emerald-700 border-emerald-200';
}

function MetricCard({ label, children }) {
  return (
    <div className="p-4 rounded-2xl bg-muted/50 border">
      <span className="text-xs text-muted-foreground font-medium block">{label}</span>
      {children}
    </div>
  );
}

function Field({ label, icon, children }) {
  return (
    <div>
      <span className="text-muted-foreground block">{label}</span>
      <span className="text-foreground font-medium flex items-center gap-1 mt-0.5">
        {icon}
        {children}
      </span>
    </div>
  );
}
