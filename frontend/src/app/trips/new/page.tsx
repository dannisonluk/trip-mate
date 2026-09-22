"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

import { api, errorMessage } from "@/lib/api";
import { RequireAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { BUDGET_OPTIONS, TARGET_GENDER_OPTIONS, TRAVEL_STYLE_OPTIONS, cn } from "@/lib/utils";
import type { BudgetType, TargetGender } from "@/lib/types";
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
import { Textarea } from "@/components/ui/textarea";

function NewTripForm() {
  const router = useRouter();
  const { t, label } = useI18n();

  const [form, setForm] = useState({
    title: "",
    description: "",
    destination_country: "",
    destination_city: "",
    start_date: "",
    end_date: "",
    budget_type: "MODERATE" as BudgetType,
    target_gender: "ANY" as TargetGender,
    looking_for_count: 1,
  });
  const [tags, setTags] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function update<K extends keyof typeof form>(key: K, value: (typeof form)[K]) {
    setForm((prev) => ({ ...prev, [key]: value }));
  }

  function toggleTag(tag: string) {
    setTags((prev) => (prev.includes(tag) ? prev.filter((t) => t !== tag) : [...prev, tag]));
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const payload: Record<string, unknown> = {
        ...form,
        tags,
        looking_for_count: Number(form.looking_for_count),
      };
      // Omit empty optionals so the API keeps them null.
      if (!payload.start_date) delete payload.start_date;
      if (!payload.end_date) delete payload.end_date;
      if (!payload.destination_city) delete payload.destination_city;

      const created = await api.createTrip(payload);
      router.push(`/trips/${created.id}`);
    } catch (err) {
      setError(errorMessage(err, t("newTrip.publishError")));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-2xl py-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-2xl">{t("nav.newTrip")}</CardTitle>
          <CardDescription>{t("newTrip.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent>
          {error && (
            <div className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
              {error}
            </div>
          )}

          <form onSubmit={onSubmit} className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="title">{t("newTrip.titleLabel")}</Label>
              <Input
                id="title"
                value={form.title}
                onChange={(e) => update("title", e.target.value)}
                minLength={4}
                maxLength={120}
                placeholder={t("newTrip.titlePlaceholder")}
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="description">{t("newTrip.descriptionLabel")}</Label>
              <Textarea
                id="description"
                value={form.description}
                onChange={(e) => update("description", e.target.value)}
                minLength={10}
                maxLength={3000}
                placeholder={t("newTrip.descriptionPlaceholder")}
                required
              />
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="country">{t("newTrip.countryLabel")}</Label>
                <Input
                  id="country"
                  value={form.destination_country}
                  onChange={(e) => update("destination_country", e.target.value)}
                  required
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="city">{t("newTrip.cityLabel")}</Label>
                <Input
                  id="city"
                  value={form.destination_city}
                  onChange={(e) => update("destination_city", e.target.value)}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="start">{t("newTrip.startLabel")}</Label>
                <Input
                  id="start"
                  type="date"
                  value={form.start_date}
                  onChange={(e) => update("start_date", e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="end">{t("newTrip.endLabel")}</Label>
                <Input
                  id="end"
                  type="date"
                  value={form.end_date}
                  onChange={(e) => update("end_date", e.target.value)}
                />
              </div>
            </div>

            <div className="grid gap-4 sm:grid-cols-3">
              <div className="space-y-2">
                <Label>{t("trips.filterBudget")}</Label>
                <Select
                  value={form.budget_type}
                  onValueChange={(v) => update("budget_type", v as BudgetType)}
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
                <Label>{t("newTrip.genderLabel")}</Label>
                <Select
                  value={form.target_gender}
                  onValueChange={(v) => update("target_gender", v as TargetGender)}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TARGET_GENDER_OPTIONS.map((value) => (
                      <SelectItem key={value} value={value}>
                        {label("gender", value)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="count">{t("newTrip.countLabel")}</Label>
                <Input
                  id="count"
                  type="number"
                  min={1}
                  max={20}
                  value={form.looking_for_count}
                  onChange={(e) => update("looking_for_count", Number(e.target.value))}
                />
              </div>
            </div>

            <div className="space-y-2">
              <Label>{t("newTrip.styleLabel")}</Label>
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
                      {label("travelStyle", tag)}
                    </button>
                  );
                })}
              </div>
            </div>

            <Button type="submit" className="w-full" disabled={busy}>
              {busy && <Loader2 className="h-4 w-4 animate-spin" />}
              {t("nav.newTrip")}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}

export default function NewTripPage() {
  return (
    <RequireAuth>
      <NewTripForm />
    </RequireAuth>
  );
}
