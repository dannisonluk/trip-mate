"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { Loader2, PlusCircle, Search, Sparkles } from "lucide-react";

import { api, errorMessage } from "@/lib/api";
import { RequireAuth, useAuth } from "@/lib/auth";
import { useI18n } from "@/lib/i18n";
import { BUDGET_OPTIONS, TRAVEL_STYLE_OPTIONS } from "@/lib/utils";
import type { BudgetType, PaginatedTrips, Recommendation } from "@/lib/types";
import DisclaimerBanner from "@/components/DisclaimerBanner";
import TripCard from "@/components/TripCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const ALL = "ALL";

function TripsContent() {
  const { user } = useAuth();
  const { t, label } = useI18n();

  const [data, setData] = useState<PaginatedTrips | null>(null);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Filters (spec: country, city, budget_type, tags, start_date, page, limit)
  const [country, setCountry] = useState("");
  const [city, setCity] = useState("");
  const [budget, setBudget] = useState<string>(ALL);
  const [tag, setTag] = useState<string>(ALL);
  const [startDate, setStartDate] = useState("");
  const [page, setPage] = useState(1);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, recommendations] = await Promise.all([
        api.listTrips({
          country: country || undefined,
          city: city || undefined,
          budget_type: budget === ALL ? undefined : (budget as BudgetType),
          tags: tag === ALL ? undefined : tag,
          start_date: startDate || undefined,
          page,
          limit: 12,
        }),
        page === 1 ? api.recommendations(4).catch(() => []) : Promise.resolve([]),
      ]);
      setData(list);
      if (page === 1) setRecs(recommendations);
    } catch (err) {
      setError(errorMessage(err, t("trips.loadError")));
    } finally {
      setLoading(false);
    }
  }, [country, city, budget, tag, startDate, page, t]);

  useEffect(() => {
    if (user) void load();
  }, [user, load]);

  function resetFilters() {
    setCountry("");
    setCity("");
    setBudget(ALL);
    setTag(ALL);
    setStartDate("");
    setPage(1);
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.limit)) : 1;

  return (
    <div className="space-y-8 py-4">
      <DisclaimerBanner />

      {recs.length > 0 && (
        <section>
          <h2 className="flex items-center gap-2 text-lg font-semibold">
            <Sparkles className="h-4 w-4 text-accent" />
            {t("trips.recommendedForYou")}
          </h2>
          <p className="mb-4 mt-1 text-sm text-muted-foreground">
            {t("trips.recommendedWhy")}
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            {recs.map((r) => (
              <div key={r.post.id} className="space-y-2">
                <TripCard post={r.post} />
                <div className="flex flex-wrap gap-1.5">
                  <Badge variant="outline">{t("trips.matchScore", { score: r.score })}</Badge>
                  {r.reasons.slice(0, 2).map((reason) => (
                    <Badge key={reason} variant="secondary">
                      {reason}
                    </Badge>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      <section>
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">{t("trips.allTrips")}</h2>
          <Button asChild size="sm">
            <Link href="/trips/new">
              <PlusCircle className="h-4 w-4" />
              {t("nav.newTrip")}
            </Link>
          </Button>
        </div>

        {/* Filter bar */}
        <Card className="mb-5">
          <CardContent className="grid gap-3 p-4 sm:grid-cols-2 lg:grid-cols-5">
            <div className="space-y-1.5">
              <Label className="text-xs">{t("trips.filterCountry")}</Label>
              <Input
                placeholder={t("trips.filterCountryPlaceholder")}
                value={country}
                onChange={(e) => {
                  setCountry(e.target.value);
                  setPage(1);
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("trips.filterCity")}</Label>
              <Input
                placeholder={t("trips.filterCityPlaceholder")}
                value={city}
                onChange={(e) => {
                  setCity(e.target.value);
                  setPage(1);
                }}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("trips.filterBudget")}</Label>
              <Select
                value={budget}
                onValueChange={(v) => {
                  setBudget(v);
                  setPage(1);
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{t("common.all")}</SelectItem>
                  {BUDGET_OPTIONS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {label("budget", value)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("trips.filterStyle")}</Label>
              <Select
                value={tag}
                onValueChange={(v) => {
                  setTag(v);
                  setPage(1);
                }}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{t("common.all")}</SelectItem>
                  {TRAVEL_STYLE_OPTIONS.map((value) => (
                    <SelectItem key={value} value={value}>
                      {label("travelStyle", value)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">{t("trips.filterStartDate")}</Label>
              <Input
                type="date"
                value={startDate}
                onChange={(e) => {
                  setStartDate(e.target.value);
                  setPage(1);
                }}
              />
            </div>
            <div className="sm:col-span-2 lg:col-span-5">
              <Button variant="ghost" size="sm" onClick={resetFilters}>
                {t("trips.resetFilters")}
              </Button>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="mb-4 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {error}
          </div>
        )}

        {loading ? (
          <div className="flex justify-center py-16 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
          </div>
        ) : data && data.items.length > 0 ? (
          <>
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {data.items.map((post) => (
                <TripCard key={post.id} post={post} />
              ))}
            </div>

            {totalPages > 1 && (
              <div className="mt-6 flex items-center justify-center gap-3">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page <= 1}
                  onClick={() => setPage((p) => p - 1)}
                >
                  {t("trips.prevPage")}
                </Button>
                <span className="text-sm text-muted-foreground">
                  {t("trips.pageIndicator", { page, totalPages, total: data.total })}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                >
                  {t("trips.nextPage")}
                </Button>
              </div>
            )}
          </>
        ) : (
          <div className="flex flex-col items-center gap-2 py-16 text-center text-muted-foreground">
            <Search className="h-5 w-5" />
            <p className="text-sm">{t("trips.empty")}</p>
          </div>
        )}
      </section>
    </div>
  );
}

export default function TripsPage() {
  return (
    <RequireAuth>
      <TripsContent />
    </RequireAuth>
  );
}
