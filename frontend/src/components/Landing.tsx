/**
 * Landing page component.
 * 
 * Public-facing marketing page with hero, features, and waitlist CTA.
 */

import { useEffect, useState } from 'react';
import { WaitlistForm } from './WaitlistForm';
import { AnimatedTextConversation } from './AnimatedTextConversation';
import demoConversation from '../data/demoConversation.json';

interface LandingProps {
  onGetStarted: () => void;
  onNavigateToBlog: () => void;
}

export function Landing({ onGetStarted, onNavigateToBlog }: LandingProps): JSX.Element {
  const [isVisible, setIsVisible] = useState<boolean>(false);
  const [showWaitlistForm, setShowWaitlistForm] = useState<boolean>(false);
  const [showWaitlistSuccess, setShowWaitlistSuccess] = useState<boolean>(false);

  useEffect(() => {
    setIsVisible(true);
  }, []);

  const handleWaitlistSuccess = (): void => {
    setShowWaitlistForm(false);
    setShowWaitlistSuccess(true);
  };

  return (
    <div className="min-h-screen bg-surface-950 overflow-hidden">
      {/* Gradient background effect */}
      <div className="fixed inset-0 overflow-hidden pointer-events-none">
        <div className="absolute -top-1/2 -right-1/4 w-[800px] h-[800px] rounded-full bg-gradient-to-br from-primary-600/20 to-transparent blur-3xl" />
        <div className="absolute -bottom-1/4 -left-1/4 w-[600px] h-[600px] rounded-full bg-gradient-to-tr from-emerald-600/10 to-transparent blur-3xl" />
      </div>

      {/* Navigation */}
      <nav className="relative z-10 flex items-center justify-between px-6 py-4 max-w-7xl mx-auto">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
            <img src="/logo.svg" alt="Revtops" className="w-5 h-5 invert" />
          </div>
          <span className="text-xl font-bold text-surface-50">Revtops</span>
        </div>
        <div className="flex items-center gap-6">
          <button
            onClick={onNavigateToBlog}
            className="px-3 py-2 text-sm font-medium text-surface-300 hover:text-white transition-colors"
          >
            Blog
          </button>
          <button
            onClick={onGetStarted}
            className="px-4 py-2 text-sm font-medium text-surface-300 hover:text-white transition-colors"
          >
            Sign In
          </button>
        </div>
      </nav>

      {/* Hero Section */}
      <section className="relative z-10 max-w-7xl mx-auto px-6 pt-20 pb-32">
        <div
          className={`text-center transition-all duration-1000 ${isVisible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-8'
            }`}
        >
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary-500/10 border border-primary-500/20 text-primary-400 text-sm mb-6">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-primary-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-primary-500"></span>
            </span>
            AI-Powered Revenue Intelligence
          </div>

          <h1 className="text-5xl md:text-7xl font-bold text-surface-50 mb-6 leading-tight">
            Your Revenue Copilot
          </h1>

          <p className="text-xl text-surface-400 max-w-3xl mx-auto mb-10">
            Imagine an AI agent connected to your CRM, Slack, meeting transcripts, sellers' emails, calendars, and so much more.
            You could ask it questions about your data to uncover insights, then build automations that supercharge your team's efficiency.
          </p>

          {/* CTA buttons */}
          <div className="flex flex-col sm:flex-row gap-4 justify-center mb-12">
            <button
              onClick={() => setShowWaitlistForm(true)}
              className="btn-primary text-lg px-8 py-4 inline-flex items-center gap-2"
            >
              Join the Waitlist
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7l5 5m0 0l-5 5m5-5H6" />
              </svg>
            </button>
            <button className="px-8 py-4 rounded-lg font-medium text-surface-300 border border-surface-700 hover:border-surface-500 transition-colors">
              Watch Demo
            </button>
          </div>

          {/* Animated conversation demo */}
          <div className="mb-12">
            <AnimatedTextConversation
              conversation={demoConversation}
              messageDelayMs={4000}
            />
          </div>

          {/* Two pillars */}
          <div className="flex flex-col sm:flex-row gap-6 justify-center max-w-2xl mx-auto">
            <div className="flex-1 p-4 rounded-xl border border-surface-700 bg-surface-900/30 text-left">
              <div className="flex items-center gap-2 mb-2">
                <svg className="w-5 h-5 text-primary-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
                <span className="font-semibold text-surface-100">Chat & Take Action</span>
              </div>
              <p className="text-sm text-surface-400">Ask questions, get insights, and update your CRM—add contacts, update deals, all via chat.</p>
            </div>
            <div className="flex-1 p-4 rounded-xl border border-surface-700 bg-surface-900/30 text-left">
              <div className="flex items-center gap-2 mb-2">
                <svg className="w-5 h-5 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                <span className="font-semibold text-surface-100">Automations</span>
              </div>
              <p className="text-sm text-surface-400">Build and launch workflows that eliminate busywork and boost team performance.</p>
            </div>
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="relative z-10 max-w-7xl mx-auto px-6 py-24">
        <h2 className="text-3xl font-bold text-surface-50 text-center mb-4">
          Insights and automation, unified
        </h2>
        <p className="text-surface-400 text-center max-w-xl mx-auto mb-16">
          Connect your tools, ask questions, and let AI-powered automations handle the rest.
        </p>

        <div className="grid md:grid-cols-2 lg:grid-cols-4 gap-6">
          {[
            {
              icon: (
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
                </svg>
              ),
              title: 'CRM Integration',
              description: 'Connect HubSpot or Salesforce. Query, add, and update deals and contacts via chat.',
              color: 'from-orange-500 to-red-500',
            },
            {
              icon: (
                <svg className="w-6 h-6" viewBox="0 0 24 24" fill="currentColor">
                  <path d="M5.042 15.165a2.528 2.528 0 0 1-2.52 2.523A2.528 2.528 0 0 1 0 15.165a2.527 2.527 0 0 1 2.522-2.52h2.52v2.52zm1.271 0a2.527 2.527 0 0 1 2.521-2.52 2.527 2.527 0 0 1 2.521 2.52v6.313A2.528 2.528 0 0 1 8.834 24a2.528 2.528 0 0 1-2.521-2.522v-6.313z" />
                </svg>
              ),
              title: 'Slack Integration',
              description: 'Search conversations, track deal discussions, and get team context instantly.',
              color: 'from-purple-500 to-pink-500',
            },
            {
              icon: (
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
                </svg>
              ),
              title: 'Calendar Sync',
              description: 'See meeting history, upcoming calls, and activity timelines with your deals.',
              color: 'from-blue-500 to-cyan-500',
            },
            {
              icon: (
                <svg className="w-6 h-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
              ),
              title: 'Smart Automations',
              description: 'Build workflows that alert your team, update records, and keep deals moving.',
              color: 'from-emerald-500 to-teal-500',
            },
          ].map((feature, i) => (
            <div
              key={i}
              className="group p-6 rounded-xl border border-surface-800 bg-surface-900/30 hover:bg-surface-900/50 transition-all hover:border-surface-700"
            >
              <div className={`w-12 h-12 rounded-lg bg-gradient-to-br ${feature.color} flex items-center justify-center text-white mb-4`}>
                {feature.icon}
              </div>
              <h3 className="text-lg font-semibold text-surface-100 mb-2">{feature.title}</h3>
              <p className="text-surface-400">{feature.description}</p>
            </div>
          ))}
        </div>
      </section>

      {/* How it Works */}
      <section className="relative z-10 max-w-7xl mx-auto px-6 py-24 border-t border-surface-800">
        <h2 className="text-3xl font-bold text-surface-50 text-center mb-16">
          Get started in minutes
        </h2>

        <div className="grid md:grid-cols-4 gap-6">
          {[
            { step: '1', title: 'Connect your tools', desc: 'Link HubSpot, Slack, and Google Calendar with secure OAuth.' },
            { step: '2', title: 'Ask or update', desc: 'Query data and make changes using natural language—no SQL or forms needed.' },
            { step: '3', title: 'Get insights', desc: 'Receive instant answers, reports, and actionable recommendations.' },
            { step: '4', title: 'Automate workflows', desc: 'Build automations that keep your team focused on high-impact work.' },
          ].map((item, i) => (
            <div key={i} className="text-center">
              <div className="w-12 h-12 rounded-full bg-primary-500/20 border border-primary-500/30 flex items-center justify-center text-primary-400 text-xl font-bold mx-auto mb-4">
                {item.step}
              </div>
              <h3 className="text-lg font-semibold text-surface-100 mb-2">{item.title}</h3>
              <p className="text-surface-400">{item.desc}</p>
            </div>
          ))}
        </div>

        <div className="text-center mt-16">
          <button
            onClick={() => setShowWaitlistForm(true)}
            className="btn-primary text-lg px-8 py-4"
          >
            Join the Waitlist
          </button>
          <p className="text-sm text-surface-500 mt-4">Be first in line for early access</p>
        </div>
      </section>

      {/* Footer */}
      <footer className="relative z-10 border-t border-surface-800 py-8">
        <div className="max-w-7xl mx-auto px-6 flex flex-col md:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded bg-gradient-to-br from-primary-500 to-primary-700 flex items-center justify-center">
              <img src="/logo.svg" alt="Revtops" className="w-3.5 h-3.5 invert" />
            </div>
            <span className="text-surface-400 text-sm">© 2026 Revtops. All rights reserved.</span>
          </div>
          <div className="flex gap-6 text-sm text-surface-500">
            <a href="#" className="hover:text-surface-300 transition-colors">Privacy</a>
            <a href="#" className="hover:text-surface-300 transition-colors">Terms</a>
            <a href="#" className="hover:text-surface-300 transition-colors">Contact</a>
          </div>
        </div>
      </footer>

      {/* Waitlist Form Modal */}
      {showWaitlistForm && (
        <WaitlistForm
          onClose={() => setShowWaitlistForm(false)}
          onSuccess={handleWaitlistSuccess}
        />
      )}

      {/* Waitlist Success Modal */}
      {showWaitlistSuccess && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm">
          <div className="w-full max-w-md p-8 rounded-2xl bg-surface-900 border border-surface-700 shadow-2xl text-center">
            <div className="w-16 h-16 rounded-full bg-emerald-500/20 flex items-center justify-center mx-auto mb-6">
              <svg className="w-8 h-8 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
              </svg>
            </div>
            <h2 className="text-2xl font-bold text-surface-50 mb-3">You're on the list!</h2>
            <p className="text-surface-400 mb-6">
              Thanks for signing up. We'll email you when it's your turn to get started with Revtops.
            </p>
            <button
              onClick={() => setShowWaitlistSuccess(false)}
              className="btn-primary px-6 py-2"
            >
              Got it
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
