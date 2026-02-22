/**
 * Subscription upgrade: select paid plan and add payment method (Stripe).
 * Shown when user clicks "Upgrade" from billing panel.
 * Free tier users are auto-enrolled, so this only shows paid plans.
 */

import { useCallback, useEffect, useState } from 'react';
import { loadStripe } from '@stripe/stripe-js';
import {
  Elements,
  useStripe,
  useElements,
  CardElement,
} from '@stripe/react-stripe-js';
import { apiRequest } from '../lib/api';

const stripePublishableKey: string | undefined =
  import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY;

// Single Stripe promise per key so we don't create multiple Stripe() instances
const stripePromiseByKey: Map<string, Promise<import('@stripe/stripe-js').Stripe | null>> = new Map();
function getStripePromise(publishableKey: string): Promise<import('@stripe/stripe-js').Stripe | null> {
  let p = stripePromiseByKey.get(publishableKey);
  if (!p) {
    p = loadStripe(publishableKey);
    stripePromiseByKey.set(publishableKey, p);
  }
  return p;
}

interface Plan {
  tier: string;
  name: string;
  price_cents: number;
  credits_included: number;
  stripe_product_id: string | null;
}

interface SubscriptionSetupProps {
  onComplete: () => void;
  onBack: () => void;
  backLabel?: string;
  currentTier?: string | null;
}

function formatPrice(cents: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 0,
  }).format(cents / 100);
}

function SubscribeForm({
  clientSecret,
  plans,
  selectedTier,
  currentTier,
  onTierChange,
  onSuccess,
  onError,
}: {
  clientSecret: string;
  plans: Plan[];
  selectedTier: string;
  currentTier: string | null;
  onTierChange: (tier: string) => void;
  onSuccess: () => void;
  onError: (msg: string) => void;
}): JSX.Element {
  const stripe = useStripe();
  const elements = useElements();
  const [loading, setLoading] = useState(false);

  const selectedPlan = plans.find((p) => p.tier === selectedTier);
  const isFreeTier = selectedPlan?.price_cents === 0;

  const handleDowngradeToFree = useCallback(async () => {
    setLoading(true);
    onError('');
    try {
      const { data, error: subError } = await apiRequest<{ status: string }>(
        '/billing/subscribe',
        {
          method: 'POST',
          body: JSON.stringify({ tier: 'free' }),
        }
      );
      if (subError || !data) {
        onError(subError ?? 'Downgrade failed');
        setLoading(false);
        return;
      }
      onSuccess();
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Something went wrong');
    } finally {
      setLoading(false);
    }
  }, [onSuccess, onError]);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (isFreeTier) {
        await handleDowngradeToFree();
        return;
      }
      if (!stripe || !elements) return;
      const cardEl = elements.getElement(CardElement);
      if (!cardEl) {
        onError('Card element not ready');
        return;
      }
      setLoading(true);
      onError('');
      try {
        const { setupIntent, error } = await stripe.confirmCardSetup(clientSecret, {
          payment_method: { card: cardEl },
        });
        if (error) {
          onError(error.message ?? 'Card confirmation failed');
          setLoading(false);
          return;
        }
        const paymentMethodId =
          typeof setupIntent?.payment_method === 'string'
            ? setupIntent.payment_method
            : (setupIntent?.payment_method as { id?: string } | undefined)?.id;
        if (!paymentMethodId) {
          onError('Could not get payment method');
          setLoading(false);
          return;
        }
        const { data, error: subError } = await apiRequest<{ status: string }>(
          '/billing/subscribe',
          {
            method: 'POST',
            body: JSON.stringify({
              payment_method_id: paymentMethodId,
              tier: selectedTier,
            }),
          }
        );
        if (subError || !data) {
          onError(subError ?? 'Subscription failed');
          setLoading(false);
          return;
        }
        onSuccess();
      } catch (err) {
        onError(err instanceof Error ? err.message : 'Something went wrong');
      } finally {
        setLoading(false);
      }
    },
    [stripe, elements, clientSecret, selectedTier, isFreeTier, handleDowngradeToFree, onSuccess, onError]
  );

  // Sort plans: free first, then by price ascending
  const sortedPlans = [...plans].sort((a, b) => {
    if (a.price_cents === 0) return -1;
    if (b.price_cents === 0) return 1;
    return a.price_cents - b.price_cents;
  });

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      <div>
        <label className="block text-sm font-medium text-surface-300 mb-2">
          Plan
        </label>
        <div className="space-y-2">
          {sortedPlans.map((plan) => {
            const isCurrent = plan.tier === currentTier;
            return (
              <label
                key={plan.tier}
                className={`flex items-center justify-between p-3 rounded-xl border cursor-pointer transition-colors ${
                  selectedTier === plan.tier
                    ? 'border-primary-500 bg-primary-500/10'
                    : 'border-surface-700 hover:border-surface-600'
                }`}
              >
                <input
                  type="radio"
                  name="tier"
                  value={plan.tier}
                  checked={selectedTier === plan.tier}
                  onChange={() => onTierChange(plan.tier)}
                  className="sr-only"
                />
                <span className="flex items-center gap-2">
                  <span className="text-surface-100 font-medium">{plan.name}</span>
                  {isCurrent && (
                    <span className="px-1.5 py-0.5 text-xs font-medium bg-surface-700 text-surface-300 rounded">
                      Current
                    </span>
                  )}
                </span>
                <span className="text-surface-400 text-sm">
                  {plan.price_cents === 0 ? 'Free' : `${formatPrice(plan.price_cents)}/mo`} · {plan.credits_included} credits
                </span>
              </label>
            );
          })}
        </div>
      </div>
      {!isFreeTier && (
        <div>
          <label className="block text-sm font-medium text-surface-300 mb-2">
            Card details
          </label>
          <div className="p-4 rounded-xl border border-surface-700 bg-surface-900/50">
            <CardElement
              options={{
                style: {
                  base: {
                    fontSize: '16px',
                    color: '#e2e8f0',
                    '::placeholder': { color: '#94a3b8' },
                  },
                  invalid: { color: '#f87171' },
                },
              }}
            />
          </div>
        </div>
      )}
      <button
        type="submit"
        disabled={(!isFreeTier && !stripe) || loading}
        className="w-full btn-primary disabled:opacity-50"
      >
        {loading
          ? isFreeTier ? 'Switching…' : 'Upgrading…'
          : isFreeTier ? 'Switch to Free' : 'Upgrade plan'}
      </button>
    </form>
  );
}

function LoadingSpinner(): JSX.Element {
  return (
    <svg
      className="animate-spin h-6 w-6 text-primary-400 mx-auto"
      xmlns="http://www.w3.org/2000/svg"
      fill="none"
      viewBox="0 0 24 24"
      aria-hidden
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
      />
    </svg>
  );
}

export function SubscriptionSetup({ onComplete, onBack, backLabel = 'Back', currentTier = null }: SubscriptionSetupProps): JSX.Element {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [clientSecret, setClientSecret] = useState<string | null>(null);
  const [selectedTier, setSelectedTier] = useState<string>('pro');
  const [error, setError] = useState<string | null>(null);
  const [loadingPlans, setLoadingPlans] = useState(true);
  const [formLoadFailed, setFormLoadFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const { data: plansData } = await apiRequest<{ plans: Plan[] }>('/billing/plans');
      if (cancelled || !plansData?.plans?.length) {
        if (!cancelled) setPlans([]);
        setLoadingPlans(false);
        return;
      }
      // Include all plans - free tier shown for downgrade option
      const allPlans = plansData.plans;
      setPlans(allPlans);
      const defaultTier = allPlans.find((p) => p.tier === 'pro')?.tier ?? allPlans[0]?.tier ?? 'pro';
      setSelectedTier(defaultTier);
      const { data: setupData } = await apiRequest<{ client_secret: string }>(
        '/billing/setup-intent',
        { method: 'POST' }
      );
      if (cancelled) return;
      if (setupData?.client_secret) {
        setClientSecret(setupData.client_secret);
      } else {
        setFormLoadFailed(true);
      }
      setLoadingPlans(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!stripePublishableKey) {
    return (
      <div className="space-y-4">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-surface-400 hover:text-surface-200 transition-colors text-sm"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          {backLabel}
        </button>
        <p className="text-surface-400 text-sm">Billing is not configured for this environment.</p>
      </div>
    );
  }

  const stripePromise = getStripePromise(stripePublishableKey);

  const showForm = Boolean(clientSecret);
  const showFormLoading = !showForm && !formLoadFailed && plans.length > 0;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button
          onClick={onBack}
          className="flex items-center gap-1.5 text-surface-400 hover:text-surface-200 transition-colors text-sm"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
          </svg>
          {backLabel}
        </button>
      </div>

      {/* Title */}
      <div>
        <h2 className="text-lg font-semibold text-surface-50">Upgrade your plan</h2>
        <p className="text-surface-400 text-sm mt-1">
          Add a payment method to unlock more credits.
        </p>
      </div>

      {/* Content */}
      {loadingPlans && plans.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-6 gap-2">
          <LoadingSpinner />
          <p className="text-surface-400 text-sm">Loading plans…</p>
        </div>
      ) : showFormLoading ? (
        <div className="flex flex-col items-center justify-center py-6 gap-2">
          <LoadingSpinner />
          <p className="text-surface-400 text-sm">Loading payment form…</p>
        </div>
      ) : formLoadFailed ? (
        <p className="text-surface-400 text-sm py-4">
          Unable to load payment form. Please try again or contact support.
        </p>
      ) : showForm ? (
        <>
          {error && (
            <div className="p-3 rounded-lg bg-red-500/10 border border-red-500/30 text-red-400 text-sm">
              {error}
            </div>
          )}
          <Elements
            stripe={stripePromise}
            options={{
              clientSecret: clientSecret!,
              appearance: { theme: 'night' as const },
            }}
          >
            <SubscribeForm
              clientSecret={clientSecret!}
              plans={plans}
              selectedTier={selectedTier}
              currentTier={currentTier}
              onTierChange={setSelectedTier}
              onSuccess={onComplete}
              onError={setError}
            />
          </Elements>
        </>
      ) : null}
    </div>
  );
}
