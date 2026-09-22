"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  CalendarDays,
  Flag,
  Loader2,
  MapPin,
  MessageSquare,
  PenLine,
  ShieldOff,
} from "lucide-react";

import { api, errorMessage } from "@/lib/api";
import { RequireAuth, useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { REVIEW_TAG_OPTIONS, cn } from "@/lib/utils";
import type { ProfilePublic, Review, ReviewSummary, TravelHistory } from "@/lib/types";
import DisclaimerBanner from "@/components/DisclaimerBanner";
import StarRating from "@/components/StarRating";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";

function ProfileDetail() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { profile: me } = useAuth();
  const { t, label, formatDate, formatDateTime } = useI18n();

  const [profile, setProfile] = useState<ProfilePublic | null>(null);
  const [histories, setHistories] = useState<TravelHistory[]>([]);
  const [reviews, setReviews] = useState<Review[]>([]);
  const [summary, setSummary] = useState<ReviewSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Review dialog
  const [reviewOpen, setReviewOpen] = useState(false);
  const [rating, setRating] = useState(5);
  const [reviewTags, setReviewTags] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);

  const isSelf = Boolean(profile && me && profile.id === me.id);

  const load = useCallback(async () => {
    if (!params?.id) return;
    setLoading(true);
    setError(null);
    try {
      const [p, h, r, s] = await Promise.all([
        api.getProfile(params.id),
        api.listHistories(params.id).catch(() => []),
        api.listReviews(params.id).catch(() => []),
        api.reviewSummary(params.id).catch(() => null),
      ]);
      setProfile(p);
      setHistories(h);
      setReviews(r);
      setSummary(s);
    } catch (err) {
      setError(errorMessage(err, t("profileView.notFound")));
    } finally {
      setLoading(false);
    }
  }, [params?.id, t]);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleMessage() {
    if (!profile) return;
    setBusy(true);
    try {
      const room = await api.createRoom({ room_type: "DIRECT", other_profile_id: profile.id });
      router.push(`/chat?room=${room.id}`);
    } catch (err) {
      setNotice(errorMessage(err, t("tripDetail.openChatError")));
    } finally {
      setBusy(false);
    }
  }

  async function handleBlock() {
    if (!profile) return;
    if (!confirm(t("tripDetail.blockConfirm"))) return;
    try {
      await api.blockProfile(profile.id);
      setNotice(t("tripDetail.blocked"));
      router.push("/trips");
    } catch (err) {
      setNotice(errorMessage(err, t("tripDetail.blockError")));
    }
  }

  async function handleReport() {
    if (!profile) return;
    const reason = prompt(
      t("tripDetail.reportPrompt"),
      "harassment",
    );
    if (!reason) return;
    try {
      await api.reportProfile({ reported_profile_id: profile.id, reason });
      setNotice(t("tripDetail.reported"));
    } catch (err) {
      setNotice(errorMessage(err, t("tripDetail.reportError")));
    }
  }

  async function submitReview() {
    if (!profile) return;
    setBusy(true);
    try {
      await api.createReview({
        reviewee_id: profile.id,
        rating,
        tags: reviewTags,
        comment: comment || undefined,
      });
      setReviewOpen(false);
      setComment("");
      setReviewTags([]);
      setRating(5);
      await load();
      setNotice(t("profileView.reviewSent"));
    } catch (err) {
      setNotice(errorMessage(err, t("profileView.reviewError")));
    } finally {
      setBusy(false);
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (error || !profile) {
    return <div className="py-24 text-center text-sm text-muted-foreground">{error}</div>;
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5 py-6">
      <DisclaimerBanner />

      {notice && (
        <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm text-primary">
          {notice}
        </div>
      )}

      {/* Header */}
      <Card>
        <CardContent className="p-6">
          <div className="flex flex-wrap items-start gap-4">
            <Avatar className="h-16 w-16">
              {profile.avatar_url && <AvatarImage src={profile.avatar_url} alt="" />}
              <AvatarFallback className="text-xl">{profile.nickname.slice(0, 1)}</AvatarFallback>
            </Avatar>

            <div className="min-w-[200px] flex-1">
              <h1 className="text-2xl font-bold">{profile.nickname}</h1>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {profile.mbti && <Badge variant="outline">{profile.mbti}</Badge>}
                {profile.gender && (
                  <Badge variant="secondary">{label("gender", profile.gender)}</Badge>
                )}
                {profile.stats.average_rating != null && (
                  <StarRating value={profile.stats.average_rating} showValue />
                )}
              </div>
              {profile.bio && (
                <p className="mt-3 text-sm leading-relaxed text-muted-foreground">{profile.bio}</p>
              )}
            </div>
          </div>

          <Separator className="my-5" />

          <dl className="grid grid-cols-3 gap-4 text-center">
            {[
              [t("profileView.statTrips"), profile.stats.trips_count],
              [t("profileView.statReviews"), summary?.count ?? profile.stats.reviews_count],
              [t("profileView.statAvg"), profile.stats.average_rating?.toFixed(1) ?? "—"],
            ].map(([heading, value]) => (
              <div key={String(heading)}>
                <dd className="text-xl font-bold">{value}</dd>
                <dt className="text-xs text-muted-foreground">{heading}</dt>
              </div>
            ))}
          </dl>

          {(profile.travel_style_tags.length > 0 || profile.languages.length > 0) && (
            <>
              <Separator className="my-5" />
              <div className="space-y-3">
                {profile.travel_style_tags.length > 0 && (
                  <div>
                    <div className="mb-1.5 text-xs font-semibold text-muted-foreground">
                      {t("profileView.travelStyles")}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {profile.travel_style_tags.map((tag) => (
                        <Badge key={tag} variant="secondary">
                          {label("travelStyle", tag)}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                {profile.languages.length > 0 && (
                  <div>
                    <div className="mb-1.5 text-xs font-semibold text-muted-foreground">
                      {t("profileView.languages")}
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {profile.languages.map((l) => (
                        <Badge key={l} variant="outline">
                          {l}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </>
          )}

          {!isSelf && (
            <div className="mt-6 flex flex-wrap gap-2">
              <Button size="sm" onClick={handleMessage} disabled={busy}>
                <MessageSquare className="h-4 w-4" />
                {t("tripDetail.dm")}
              </Button>
              <Dialog open={reviewOpen} onOpenChange={setReviewOpen}>
                <DialogTrigger asChild>
                  <Button size="sm" variant="outline">
                    <PenLine className="h-4 w-4" />
                    {t("profileView.writeReview")}
                  </Button>
                </DialogTrigger>
                <DialogContent>
                  <DialogHeader>
                    <DialogTitle>
                      {t("profileView.reviewTitle", { nickname: profile.nickname })}
                    </DialogTitle>
                    <DialogDescription>
                      {t("profileView.reviewHint")}
                    </DialogDescription>
                  </DialogHeader>

                  <div className="space-y-4">
                    <div className="space-y-2">
                      <Label>{t("profileView.ratingLabel")}</Label>
                      <div className="flex gap-1">
                        {[1, 2, 3, 4, 5].map((n) => (
                          <button
                            key={n}
                            type="button"
                            onClick={() => setRating(n)}
                            className={cn(
                              "h-9 w-9 rounded-md border text-sm font-semibold transition-colors",
                              n <= rating
                                ? "border-accent bg-accent/15 text-accent"
                                : "border-border text-muted-foreground",
                            )}
                          >
                            {n}
                          </button>
                        ))}
                      </div>
                    </div>

                    <div className="space-y-2">
                      <Label>{t("profileView.tagsLabel")}</Label>
                      <div className="flex flex-wrap gap-1.5">
                        {REVIEW_TAG_OPTIONS.map((tag) => {
                          const active = reviewTags.includes(tag);
                          return (
                            <button
                              key={tag}
                              type="button"
                              onClick={() =>
                                setReviewTags((prev) =>
                                  prev.includes(tag)
                                    ? prev.filter((x) => x !== tag)
                                    : [...prev, tag],
                                )
                              }
                              className={cn(
                                "rounded-full border px-3 py-1 text-xs font-semibold transition-colors",
                                active
                                  ? "border-primary bg-primary/10 text-primary"
                                  : "border-border text-muted-foreground hover:bg-secondary",
                              )}
                            >
                              {label("reviewTag", tag)}
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="review-comment">{t("profileView.commentLabel")}</Label>
                      <Textarea
                        id="review-comment"
                        value={comment}
                        onChange={(e) => setComment(e.target.value)}
                        maxLength={1000}
                        placeholder={t("profileView.commentPlaceholder")}
                      />
                    </div>
                  </div>

                  <DialogFooter>
                    <Button variant="outline" onClick={() => setReviewOpen(false)}>
                      {t("common.cancel")}
                    </Button>
                    <Button onClick={submitReview} disabled={busy}>
                      {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                      {t("profileView.submitReview")}
                    </Button>
                  </DialogFooter>
                </DialogContent>
              </Dialog>

              <Button size="sm" variant="ghost" onClick={handleBlock}>
                <ShieldOff className="h-4 w-4" />
                {t("tripDetail.block")}
              </Button>
              <Button size="sm" variant="ghost" onClick={handleReport}>
                <Flag className="h-4 w-4" />
                {t("tripDetail.report")}
              </Button>
            </div>
          )}

          {isSelf && (
            <Button asChild size="sm" variant="outline" className="mt-6">
              <Link href="/profile">{t("profileView.editMine")}</Link>
            </Button>
          )}
        </CardContent>
      </Card>

      {/* Travel histories */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">
            {t("profileView.histories", { count: histories.length })}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {histories.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {t("profileView.noHistories")}
            </p>
          ) : (
            histories.map((h) => (
              <div key={h.id} className="rounded-lg border p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="inline-flex items-center gap-1 text-sm font-semibold">
                    <MapPin className="h-3.5 w-3.5" />
                    {h.city ? `${h.city}, ` : ""}
                    {h.country}
                  </span>
                  <Badge variant="accent">{label("budget", h.budget_type)}</Badge>
                  {(h.start_date || h.end_date) && (
                    <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                      <CalendarDays className="h-3.5 w-3.5" />
                      {formatDate(h.start_date)}
                      {h.end_date ? ` – ${formatDate(h.end_date)}` : ""}
                    </span>
                  )}
                  {!h.is_public && (
                    <Badge variant="secondary" className="ml-auto">
                      {t("profileView.private")}
                    </Badge>
                  )}
                </div>
                {h.summary && (
                  <p className="mt-2 text-sm text-muted-foreground">{h.summary}</p>
                )}
              </div>
            ))
          )}
        </CardContent>
      </Card>

      {/* Reviews */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            {t("profileView.reviews", { count: summary?.count ?? 0 })}
            {summary?.average_rating != null && (
              <StarRating value={summary.average_rating} showValue />
            )}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {reviews.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              {t("profileView.noReviews")}
            </p>
          ) : (
            reviews.map((r) => (
              <div key={r.id} className="rounded-lg border p-4">
                <div className="flex flex-wrap items-center gap-2">
                  <Avatar className="h-7 w-7">
                    <AvatarFallback className="text-xs">
                      {r.reviewer?.nickname?.slice(0, 1) ?? "?"}
                    </AvatarFallback>
                  </Avatar>
                  <span className="text-sm font-semibold">
                    {r.reviewer?.nickname ?? t("tripDetail.traveller")}
                  </span>
                  <StarRating value={r.rating} />
                  <span className="ml-auto text-xs text-muted-foreground">
                    {formatDateTime(r.created_at)}
                  </span>
                </div>
                {r.tags.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {r.tags.map((t) => (
                      <Badge key={t} variant="secondary">
                        {t}
                      </Badge>
                    ))}
                  </div>
                )}
                {r.comment && <p className="mt-2 text-sm">{r.comment}</p>}
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export default function ProfileDetailPage() {
  return (
    <RequireAuth>
      <ProfileDetail />
    </RequireAuth>
  );
}
