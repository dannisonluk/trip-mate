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
  ShieldOff,
  UserPlus,
  Users,
} from "lucide-react";

import { api, errorMessage } from "@/lib/api";
import { RequireAuth, useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import type { TripPostDetail } from "@/lib/types";
import DisclaimerBanner from "@/components/DisclaimerBanner";
import SafeHtml from "@/components/SafeHtml";
import { CityMapLoader } from "@/components/CityMapLoader";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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

function TripDetail() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const { profile } = useAuth();
  const { t, label, formatDate, formatDateTime } = useI18n();

  const [trip, setTrip] = useState<TripPostDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [applyOpen, setApplyOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    if (!params?.id) return;
    setLoading(true);
    try {
      setTrip(await api.getTrip(params.id));
    } catch (err) {
      setError(errorMessage(err, t("tripDetail.notFound")));
    } finally {
      setLoading(false);
    }
  }, [params?.id, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const isOwner = Boolean(trip && profile && trip.creator_id === profile.id);
  // A group needs at least one accepted companion to be worth opening; the
  // backend seeds membership from ACCEPTED applications only.
  const hasAcceptedApplicant = Boolean(
    trip?.applications?.some((a) => a.status === "ACCEPTED"),
  );

  async function handleApply() {
    if (!trip) return;
    setBusy(true);
    try {
      await api.applyToTrip(trip.id, message);
      setApplyOpen(false);
      setMessage("");
      setNotice(t("tripDetail.applied"));
    } catch (err) {
      setNotice(errorMessage(err, t("tripDetail.applyError"), t));
    } finally {
      setBusy(false);
    }
  }

  async function handleMessageCreator() {
    if (!trip?.creator) return;
    setBusy(true);
    try {
      const room = await api.createRoom({
        room_type: "DIRECT",
        other_profile_id: trip.creator.id,
      });
      router.push(`/chat?room=${room.id}`);
    } catch (err) {
      setNotice(errorMessage(err, t("tripDetail.openChatError")));
    } finally {
      setBusy(false);
    }
  }

  /** Open (or create) the group room for this trip.
   *
   *  Reuses an existing room when there is one — the backend also de-dupes, but
   *  checking here avoids a pointless request and lets us land the user on the
   *  right room even if the create call would have been rejected.
   */
  async function handleOpenTripGroup() {
    if (!trip) return;
    setBusy(true);
    try {
      const rooms = await api.listRooms();
      const existing = rooms.find(
        (r) => r.room_type === "TRIP" && r.trip_post_id === trip.id,
      );
      if (existing) {
        router.push(`/chat?room=${existing.id}`);
        return;
      }
      const room = await api.createRoom({
        room_type: "TRIP",
        trip_post_id: trip.id,
        title: trip.title,
      });
      router.push(`/chat?room=${room.id}`);
    } catch (err) {
      setNotice(errorMessage(err, t("tripDetail.openGroupError")));
    } finally {
      setBusy(false);
    }
  }

  async function handleBlock() {
    if (!trip?.creator) return;
    if (!confirm(t("tripDetail.blockConfirm"))) return;
    try {
      await api.blockProfile(trip.creator.id);
      setNotice(t("tripDetail.blocked"));
      router.push("/trips");
    } catch (err) {
      setNotice(errorMessage(err, t("tripDetail.blockError")));
    }
  }

  async function handleReport() {
    if (!trip?.creator) return;
    const reason = prompt(
      t("tripDetail.reportPrompt"),
      "harassment",
    );
    if (!reason) return;
    try {
      await api.reportProfile({ reported_profile_id: trip.creator.id, reason });
      setNotice(t("tripDetail.reported"));
    } catch (err) {
      setNotice(errorMessage(err, t("tripDetail.reportError")));
    }
  }

  async function decide(applicationId: string, decision: "ACCEPTED" | "REJECTED") {
    try {
      await api.decideApplication(applicationId, decision);
      await load();
      setNotice(decision === "ACCEPTED" ? t("tripDetail.accepted") : t("tripDetail.rejected"));
    } catch (err) {
      setNotice(errorMessage(err, t("tripDetail.actionError")));
    }
  }

  if (loading) {
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (error || !trip) {
    return <div className="py-24 text-center text-sm text-muted-foreground">{error}</div>;
  }

  const start = formatDate(trip.start_date);
  const end = formatDate(trip.end_date);

  return (
    <div className="mx-auto max-w-3xl space-y-5 py-6">
      <DisclaimerBanner />

      {notice && (
        <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm text-primary">
          {notice}
        </div>
      )}

      <Card>
        <CardHeader>
          <div className="mb-2 flex flex-wrap items-center gap-1.5">
            <Badge variant="accent">{label("budget", trip.budget_type)}</Badge>
            <Badge variant="secondary">
              {t("tripDetail.lookingForGender", { value: label("gender", trip.target_gender) })}
            </Badge>
            <Badge variant={trip.status === "OPEN" ? "default" : "secondary"}>
              {label("tripStatus", trip.status)}
            </Badge>
          </div>
          <CardTitle className="text-2xl">{trip.title}</CardTitle>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-sm text-muted-foreground">
            <span className="inline-flex items-center gap-1">
              <MapPin className="h-4 w-4" />
              {trip.destination_city ? `${trip.destination_city}, ` : ""}
              {trip.destination_country}
            </span>
            {(start || end) && (
              <span className="inline-flex items-center gap-1">
                <CalendarDays className="h-4 w-4" />
                {start}
                {end ? ` – ${end}` : ""}
              </span>
            )}
            <span className="inline-flex items-center gap-1">
              <Users className="h-4 w-4" />
              {t("tripDetail.lookingForCount", { count: trip.looking_for_count })}
            </span>
          </div>
        </CardHeader>

        <CardContent className="space-y-5">
          {trip.tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {trip.tags.map((tag) => (
                <Badge key={tag} variant="secondary">
                  {tag}
                </Badge>
              ))}
            </div>
          )}

          <SafeHtml
            html={trip.description.replace(/\n/g, "<br/>")}
            className="text-sm leading-relaxed text-muted-foreground"
          />

          {/* Optional and purely additive: renders nothing when the trip has no
              city, and degrades to plain text when the city no longer resolves.
              It must never be the only place the destination is stated — the
              header already carries city + country, and the map's accessible
              name repeats them. */}
          <CityMapLoader cityId={trip.city_id ?? null} />

          {trip.creator && (
            <>
              <Separator />
              <div className="flex flex-wrap items-center gap-3">
                <Link
                  href={`/profile/${trip.creator.id}`}
                  className="flex items-center gap-3 rounded-md p-1 transition-colors hover:bg-secondary"
                >
                  <Avatar>
                    {trip.creator.avatar_url && (
                      <AvatarImage src={trip.creator.avatar_url} alt="" />
                    )}
                    <AvatarFallback>{trip.creator.nickname.slice(0, 1)}</AvatarFallback>
                  </Avatar>
                  <div>
                    <div className="text-sm font-semibold">{trip.creator.nickname}</div>
                    {trip.creator.mbti && (
                      <div className="text-xs text-muted-foreground">{trip.creator.mbti}</div>
                    )}
                  </div>
                </Link>

                <div className="flex-1" />

                {!isOwner && (
                  <div className="flex flex-wrap gap-2">
                    <Button variant="outline" size="sm" onClick={handleMessageCreator} disabled={busy}>
                      <MessageSquare className="h-4 w-4" />
                      {t("tripDetail.dm")}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={handleBlock}>
                      <ShieldOff className="h-4 w-4" />
                      {t("tripDetail.block")}
                    </Button>
                    <Button variant="ghost" size="sm" onClick={handleReport}>
                      <Flag className="h-4 w-4" />
                      {t("tripDetail.report")}
                    </Button>
                  </div>
                )}
              </div>
            </>
          )}

          {!isOwner && trip.status === "OPEN" && (
            <Dialog open={applyOpen} onOpenChange={setApplyOpen}>
              <DialogTrigger asChild>
                <Button className="w-full">
                  <UserPlus className="h-4 w-4" />
                  {t("tripDetail.applyCta")}
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>{t("tripDetail.applyCta")}</DialogTitle>
                  <DialogDescription>
                    {t("tripDetail.applyDialogBody")}
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-2">
                  <Label htmlFor="apply-message">{t("tripDetail.applyMessageLabel")}</Label>
                  <Textarea
                    id="apply-message"
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    maxLength={1000}
                    placeholder={t("tripDetail.applyMessagePlaceholder")}
                  />
                </div>
                <DialogFooter>
                  <Button variant="outline" onClick={() => setApplyOpen(false)}>
                    {t("common.cancel")}
                  </Button>
                  <Button onClick={handleApply} disabled={busy}>
                    {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                    {t("tripDetail.submitApplication")}
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          )}
        </CardContent>
      </Card>

      {isOwner && (
        <Card>
          <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
            <div>
              <CardTitle className="text-base">
                {t("tripDetail.applications", { count: trip.applications.length })}
              </CardTitle>
              <CardDescription className="mt-1">
                {t("tripDetail.applicationsHint")}
              </CardDescription>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="shrink-0 gap-1.5"
              disabled={busy || !hasAcceptedApplicant}
              onClick={handleOpenTripGroup}
              title={
                hasAcceptedApplicant
                  ? t("tripDetail.openGroup")
                  : t("tripDetail.groupNeedsMember")
              }
            >
              <Users className="h-4 w-4" />
              {t("tripDetail.group")}
            </Button>
          </CardHeader>
          <CardContent className="space-y-3">
            {trip.applications.length === 0 ? (
              <p className="py-6 text-center text-sm text-muted-foreground">
                {t("tripDetail.noApplications")}
              </p>
            ) : (
              trip.applications.map((a) => (
                <div key={a.id} className="rounded-lg border p-4">
                  <div className="flex flex-wrap items-center gap-2">
                    <Link
                      href={`/profile/${a.applicant_id}`}
                      className="flex items-center gap-2 hover:underline"
                    >
                      <Avatar className="h-7 w-7">
                        <AvatarFallback className="text-xs">
                          {a.applicant?.nickname?.slice(0, 1) ?? "?"}
                        </AvatarFallback>
                      </Avatar>
                      <span className="text-sm font-semibold">
                        {a.applicant?.nickname ?? t("tripDetail.traveller")}
                      </span>
                    </Link>
                    <Badge
                      variant={
                        a.status === "ACCEPTED"
                          ? "default"
                          : a.status === "REJECTED"
                            ? "destructive"
                            : "secondary"
                      }
                    >
                      {label("applicationStatus", a.status)}
                    </Badge>
                    <span className="ml-auto text-xs text-muted-foreground">
                      {formatDateTime(a.created_at)}
                    </span>
                  </div>

                  {a.message && <p className="mt-3 text-sm">{a.message}</p>}

                  {a.status === "PENDING" && (
                    <div className="mt-3 flex gap-2">
                      <Button size="sm" onClick={() => decide(a.id, "ACCEPTED")}>
                        {t("tripDetail.accept")}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => decide(a.id, "REJECTED")}
                      >
                        {t("tripDetail.reject")}
                      </Button>
                    </div>
                  )}
                </div>
              ))
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export default function TripDetailPage() {
  return (
    <RequireAuth>
      <TripDetail />
    </RequireAuth>
  );
}
