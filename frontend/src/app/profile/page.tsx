"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { BadgeCheck, Loader2, Plus, Trash2 } from "lucide-react";

import { api, errorMessage } from "@/lib/api";
import { RequireAuth, useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { BUDGET_OPTIONS, TRAVEL_STYLE_OPTIONS, cn } from "@/lib/utils";
import type { BudgetType, Gender, TravelHistory } from "@/lib/types";
import AvatarUploader from "@/components/AvatarUploader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

const MBTI_OPTIONS = [
  "INTJ", "INTP", "ENTJ", "ENTP",
  "INFJ", "INFP", "ENFJ", "ENFP",
  "ISTJ", "ISFJ", "ESTJ", "ESFJ",
  "ISTP", "ISFP", "ESTP", "ESFP",
];

function MyProfile() {
  const { user, profile, refreshProfile, logout } = useAuth();
  const { t, label, formatDate } = useI18n();
  const router = useRouter();

  const [nickname, setNickname] = useState("");
  const [bio, setBio] = useState("");
  const [mbti, setMbti] = useState<string>("");
  const [gender, setGender] = useState<string>("");
  const [tags, setTags] = useState<string[]>([]);
  const [languages, setLanguages] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [histories, setHistories] = useState<TravelHistory[]>([]);
  const [newHistory, setNewHistory] = useState({
    country: "",
    city: "",
    start_date: "",
    end_date: "",
    budget_type: "MODERATE" as BudgetType,
    summary: "",
    is_public: true,
  });

  const [deletePassword, setDeletePassword] = useState("");
  const [deleteMode, setDeleteMode] = useState("anonymize");

  useEffect(() => {
    if (!profile) return;
    setNickname(profile.nickname);
    setBio(profile.bio ?? "");
    setMbti(profile.mbti ?? "");
    setGender(profile.gender ?? "");
    setTags(profile.travel_style_tags ?? []);
    setLanguages((profile.languages ?? []).join(", "));
  }, [profile]);

  useEffect(() => {
    if (profile) void api.listHistories(profile.id).then(setHistories).catch(() => {});
  }, [profile]);

  function toggleTag(tag: string) {
    setTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
  }

  async function saveProfile() {
    setBusy(true);
    setNotice(null);
    try {
      await api.updateProfile({
        nickname,
        bio,
        mbti: mbti || null,
        gender: gender || null,
        travel_style_tags: tags,
        languages: languages
          .split(",")
          .map((s) => s.trim().toUpperCase())
          .filter(Boolean),
      });
      await refreshProfile();
      setNotice(t("myProfile.saved"));
    } catch (err) {
      setNotice(errorMessage(err, t("myProfile.saveError")));
    } finally {
      setBusy(false);
    }
  }

  async function addHistory() {
    if (!newHistory.country) return;
    setNotice(null);
    try {
      const payload: Record<string, unknown> = { ...newHistory };
      if (!payload.start_date) delete payload.start_date;
      if (!payload.end_date) delete payload.end_date;
      if (!payload.city) delete payload.city;
      if (!payload.summary) delete payload.summary;

      const created = await api.addHistory(payload);
      setHistories((prev) => [created, ...prev]);
      setNewHistory({
        country: "",
        city: "",
        start_date: "",
        end_date: "",
        budget_type: "MODERATE",
        summary: "",
        is_public: true,
      });
      setNotice(t("myProfile.historyAdded"));
    } catch (err) {
      setNotice(errorMessage(err, t("myProfile.addError")));
    }
  }

  async function removeHistory(id: string) {
    try {
      await api.deleteHistory(id);
      setHistories((prev) => prev.filter((h) => h.id !== id));
    } catch (err) {
      setNotice(errorMessage(err, t("myProfile.deleteError")));
    }
  }

  async function deleteAccount() {
    if (!confirm(t("myProfile.eraseConfirm"))) return;
    try {
      await api.deleteAccount({ password: deletePassword, mode: deleteMode });
      await logout();
      router.push("/");
    } catch (err) {
      setNotice(errorMessage(err, t("myProfile.eraseError")));
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-5 py-6">
      {notice && (
        <div className="rounded-md border border-primary/30 bg-primary/5 px-3 py-2 text-sm text-primary">
          {notice}
        </div>
      )}

      <Card>
        <CardContent className="flex flex-wrap items-center gap-5 p-6">
          <AvatarUploader />
          <div className="flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-bold">{profile?.nickname}</h1>
              {user?.is_verified ? (
                <Badge className="gap-1">
                  <BadgeCheck className="h-3 w-3" />
                  {t("myProfile.verified")}
                </Badge>
              ) : (
                <Badge variant="secondary">{t("myProfile.unverified")}</Badge>
              )}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">{user?.phone_number}</p>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="profile">
        <TabsList className="w-full sm:w-auto">
          <TabsTrigger value="profile">{t("myProfile.tabProfile")}</TabsTrigger>
          <TabsTrigger value="histories">{t("myProfile.tabHistories")}</TabsTrigger>
          <TabsTrigger value="privacy">{t("myProfile.tabPrivacy")}</TabsTrigger>
        </TabsList>

        {/* --- Profile --- */}
        <TabsContent value="profile">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("myProfile.tabProfile")}</CardTitle>
              <CardDescription>{t("myProfile.profileSubtitle")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="space-y-2">
                <Label htmlFor="nickname">{t("myProfile.nicknameLabel")}</Label>
                <Input
                  id="nickname"
                  value={nickname}
                  onChange={(e) => setNickname(e.target.value)}
                  minLength={2}
                  maxLength={60}
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="bio">{t("myProfile.bioLabel")}</Label>
                <Textarea
                  id="bio"
                  value={bio}
                  onChange={(e) => setBio(e.target.value)}
                  maxLength={1000}
                />
              </div>

              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-2">
                  <Label>MBTI</Label>
                  <Select value={mbti || "NONE"} onValueChange={(v) => setMbti(v === "NONE" ? "" : v)}>
                    <SelectTrigger>
                      <SelectValue placeholder={t("myProfile.notSet")} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="NONE">{t("myProfile.notSet")}</SelectItem>
                      {MBTI_OPTIONS.map((m) => (
                        <SelectItem key={m} value={m}>
                          {m}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>{t("myProfile.genderLabel")}</Label>
                  <Select
                    value={gender || "NONE"}
                    onValueChange={(v) => setGender(v === "NONE" ? "" : v)}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder={t("myProfile.notSet")} />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="NONE">{t("myProfile.notSet")}</SelectItem>
                      {(["MALE", "FEMALE", "OTHER"] as Gender[]).map((g) => (
                        <SelectItem key={g} value={g}>
                          {label("gender", g)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              </div>

              <div className="space-y-2">
                <Label>{t("myProfile.styleLabel")}</Label>
                <div className="flex flex-wrap gap-1.5">
                  {TRAVEL_STYLE_OPTIONS.map((tag) => {
                    const active = tags.includes(tag);
                    return (
                      <button
                        key={tag}
                        type="button"
                        onClick={() => toggleTag(tag)}
                        className={cn(
                          "rounded-full border px-3 py-1 text-xs font-semibold transition-colors",
                          active
                            ? "border-primary bg-primary/10 text-primary"
                            : "border-border text-muted-foreground hover:bg-secondary",
                        )}
                      >
                        {tag}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="languages">{t("myProfile.languagesLabel")}</Label>
                <Input
                  id="languages"
                  value={languages}
                  onChange={(e) => setLanguages(e.target.value)}
                  placeholder="CANTONESE, ENGLISH, MANDARIN"
                />
              </div>

              <Button onClick={saveProfile} disabled={busy}>
                {busy && <Loader2 className="h-4 w-4 animate-spin" />}
                {t("myProfile.saveChanges")}
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        {/* --- Histories --- */}
        <TabsContent value="histories">
          <div className="space-y-5">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">{t("myProfile.addHistoryTitle")}</CardTitle>
                <CardDescription>
                  {t("myProfile.addHistoryHint")}
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-4 sm:grid-cols-2">
                  <div className="space-y-2">
                    <Label>{t("myProfile.countryLabel")}</Label>
                    <Input
                      value={newHistory.country}
                      onChange={(e) => setNewHistory({ ...newHistory, country: e.target.value })}
                      placeholder="Japan"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>{t("myProfile.cityLabel")}</Label>
                    <Input
                      value={newHistory.city}
                      onChange={(e) => setNewHistory({ ...newHistory, city: e.target.value })}
                      placeholder="Tokyo"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>{t("myProfile.startDateLabel")}</Label>
                    <Input
                      type="date"
                      value={newHistory.start_date}
                      onChange={(e) =>
                        setNewHistory({ ...newHistory, start_date: e.target.value })
                      }
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>{t("myProfile.endDateLabel")}</Label>
                    <Input
                      type="date"
                      value={newHistory.end_date}
                      onChange={(e) => setNewHistory({ ...newHistory, end_date: e.target.value })}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>{t("trips.filterBudget")}</Label>
                    <Select
                      value={newHistory.budget_type}
                      onValueChange={(v) =>
                        setNewHistory({ ...newHistory, budget_type: v as BudgetType })
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {BUDGET_OPTIONS.map((value) => (
                          <SelectItem key={value} value={value}>
                            {label("budget", value)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <Label>{t("myProfile.visibilityLabel")}</Label>
                    <Select
                      value={newHistory.is_public ? "public" : "private"}
                      onValueChange={(v) =>
                        setNewHistory({ ...newHistory, is_public: v === "public" })
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="public">{t("myProfile.public")}</SelectItem>
                        <SelectItem value="private">{t("profileView.private")}</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                </div>

                <div className="space-y-2">
                  <Label>{t("myProfile.summaryLabel")}</Label>
                  <Textarea
                    value={newHistory.summary}
                    onChange={(e) => setNewHistory({ ...newHistory, summary: e.target.value })}
                    maxLength={1000}
                    placeholder={t("myProfile.summaryPlaceholder")}
                  />
                </div>

                <Button onClick={addHistory} disabled={!newHistory.country}>
                  <Plus className="h-4 w-4" />
                  {t("myProfile.addHistory")}
                </Button>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">
                  {t("myProfile.myHistories", { count: histories.length })}
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {histories.length === 0 ? (
                  <p className="py-6 text-center text-sm text-muted-foreground">
                    {t("myProfile.noHistories")}
                  </p>
                ) : (
                  histories.map((h) => (
                    <div key={h.id} className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
                      <span className="text-sm font-semibold">
                        {h.city ? `${h.city}, ` : ""}
                        {h.country}
                      </span>
                      <Badge variant="accent">{label("budget", h.budget_type)}</Badge>
                      {(h.start_date || h.end_date) && (
                        <span className="text-xs text-muted-foreground">
                          {formatDate(h.start_date)}
                          {h.end_date ? ` – ${formatDate(h.end_date)}` : ""}
                        </span>
                      )}
                      {!h.is_public && (
                        <Badge variant="secondary">{t("profileView.private")}</Badge>
                      )}
                      <Button
                        variant="ghost"
                        size="icon"
                        className="ml-auto h-8 w-8"
                        onClick={() => removeHistory(h.id)}
                        aria-label={t("myProfile.deleteAria")}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ))
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        {/* --- Privacy --- */}
        <TabsContent value="privacy">
          <Card className="border-destructive/40">
            <CardHeader>
              <CardTitle className="text-base text-destructive">
                {t("myProfile.privacyTitle")}
              </CardTitle>
              <CardDescription>
                {t("myProfile.privacyBody")}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <Separator />
              <div className="grid gap-3 sm:grid-cols-[1fr_180px_auto]">
                <Input
                  type="password"
                  placeholder={t("myProfile.passwordPlaceholder")}
                  value={deletePassword}
                  onChange={(e) => setDeletePassword(e.target.value)}
                />
                <Select value={deleteMode} onValueChange={setDeleteMode}>
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="anonymize">{t("myProfile.modeAnonymize")}</SelectItem>
                    <SelectItem value="hard_delete">{t("myProfile.modeHardDelete")}</SelectItem>
                  </SelectContent>
                </Select>
                <Button variant="destructive" onClick={deleteAccount} disabled={!deletePassword}>
                  {t("myProfile.eraseAccount")}
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function MyProfilePage() {
  return (
    <RequireAuth>
      <MyProfile />
    </RequireAuth>
  );
}
