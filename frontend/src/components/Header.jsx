import React from 'react';
import {
  UserCheck, Landmark, RefreshCw,
  LayoutDashboard, ListChecks, Users, MapPin, Scale,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from '@/components/ui/tooltip';

const NAV_TABS = [
  { id: 'overview', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'queue', label: 'Priority Queue', icon: ListChecks },
  { id: 'mps', label: 'MPs', icon: Users },
  { id: 'states', label: 'States', icon: MapPin },
  { id: 'compare', label: 'Compare', icon: Scale },
];

export default function Header({
  activeTab,
  setActiveTab,
  currentRole,
  setCurrentRole,
  onRoleSelect,
  syncStatus,
  onTriggerSync,
  isSyncing,
  house,
  setHouse,
  loggedInUser,
  onLogout,
}) {
  const handleRoleChange = (selectedRole) => {
    if (onRoleSelect) {
      onRoleSelect(selectedRole);
    } else {
      setCurrentRole(selectedRole);
    }
  };

  return (
    <TooltipProvider delayDuration={150}>
      <header className="glass-panel border-b sticky top-0 z-40 px-3 sm:px-6 py-2 transition-all bg-white/95 backdrop-blur-md shadow-2xs">
        <div className="max-w-7xl mx-auto flex flex-col lg:flex-row lg:items-center lg:justify-between gap-2.5 lg:gap-4">
          
          {/* Top Row on Mobile / Left Section on Desktop: Prominent Brand Logo */}
          <div className="flex items-center justify-between shrink-0">
            <div
              className="flex items-center gap-2 sm:gap-3 shrink-0 select-none cursor-pointer py-1"
              onClick={() => setActiveTab('overview')}
            >
              <img
                src="/brand/jannidhi-brand.png"
                alt="JanNidhi brand logo"
                className="h-12 sm:h-14 lg:h-16 w-auto object-contain drop-shadow-xs transition-all"
              />
            </div>

            {/* Mobile Action Controls (< lg only) */}
            <div className="flex lg:hidden items-center gap-2 shrink-0">
              <Select value={house || "ALL"} onValueChange={(v) => setHouse(v === "ALL" ? '' : v)}>
                <SelectTrigger className="h-8 text-[11px] px-2.5 rounded-lg border-slate-200 bg-white">
                  <Landmark className="w-3 h-3 text-indigo-600 shrink-0 mr-1" />
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="ALL" className="text-xs">Both Houses</SelectItem>
                  <SelectItem value="Lok Sabha" className="text-xs">Lok Sabha</SelectItem>
                  <SelectItem value="Rajya Sabha" className="text-xs">Rajya Sabha</SelectItem>
                </SelectContent>
              </Select>

              {currentRole === 'MoSPI Reviewer' && onTriggerSync && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => onTriggerSync('live')}
                  disabled={isSyncing}
                  className="h-8 px-2 rounded-lg border-indigo-200 bg-indigo-50 text-indigo-700 text-[11px] font-semibold"
                >
                  <RefreshCw className={`w-3 h-3 ${isSyncing ? 'animate-spin' : ''}`} />
                  <span className="hidden sm:inline">{isSyncing ? 'Syncing...' : 'Sync'}</span>
                </Button>
              )}
            </div>
          </div>

          {/* Center Section: Navigation Tabs (Fully Mobile Compatible Scrollable Bar) */}
          <nav className="flex items-center justify-start lg:justify-center gap-1 bg-slate-100/90 dark:bg-muted/60 border border-slate-200/80 dark:border-border/80 p-1 rounded-xl shadow-2xs overflow-x-auto max-w-full scrollbar-none">
            {NAV_TABS.map((tab) => {
              const Icon = tab.icon;
              const isActive = activeTab === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-1.5 h-8 px-3 sm:px-3.5 rounded-lg text-xs font-semibold transition-all duration-150 whitespace-nowrap cursor-pointer select-none ${
                    isActive
                      ? 'bg-primary text-primary-foreground shadow-xs shadow-primary/25'
                      : 'text-slate-600 dark:text-muted-foreground hover:text-slate-900 dark:hover:text-foreground hover:bg-white/80 dark:hover:bg-accent/50'
                  }`}
                >
                  <Icon className={`w-3.5 h-3.5 shrink-0 ${isActive ? 'text-primary-foreground' : 'text-slate-500'}`} />
                  <span>{tab.label}</span>
                </button>
              );
            })}
          </nav>

          {/* Right Section: Controls & Role Switcher on Desktop */}
          <div className="hidden lg:flex items-center justify-end gap-2.5 shrink-0">
            {/* House Scope Filter */}
            <Select value={house || "ALL"} onValueChange={(v) => setHouse(v === "ALL" ? '' : v)}>
              <SelectTrigger className="h-9 min-w-[130px] px-3 gap-2 rounded-xl border-slate-200/90 bg-white text-xs font-medium shadow-2xs hover:bg-slate-50 transition-colors focus:ring-primary/20 cursor-pointer">
                <Landmark className="w-3.5 h-3.5 text-indigo-600 shrink-0" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end" className="rounded-xl shadow-lg border-slate-200/80">
                <SelectItem value="ALL" className="text-xs cursor-pointer">Both Houses</SelectItem>
                <SelectItem value="Lok Sabha" className="text-xs cursor-pointer">Lok Sabha</SelectItem>
                <SelectItem value="Rajya Sabha" className="text-xs cursor-pointer">Rajya Sabha</SelectItem>
              </SelectContent>
            </Select>

            {/* Live Sync Action Button */}
            {currentRole === 'MoSPI Reviewer' && onTriggerSync && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => onTriggerSync('live')}
                disabled={isSyncing}
                className="h-9 px-3 rounded-xl border-indigo-200 bg-indigo-50/80 text-indigo-700 hover:bg-indigo-100 hover:text-indigo-800 text-xs font-semibold shadow-2xs gap-1.5 cursor-pointer"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${isSyncing ? 'animate-spin' : ''}`} />
                <span>{isSyncing ? 'Syncing...' : 'Sync Live Data'}</span>
              </Button>
            )}

            {/* RBAC Role Switcher */}
            <Select value={currentRole} onValueChange={handleRoleChange}>
              <SelectTrigger className="h-9 min-w-[150px] px-3 gap-2 rounded-xl border-slate-200/90 bg-white text-xs font-medium shadow-2xs hover:bg-slate-50 transition-colors focus:ring-primary/20 cursor-pointer">
                <UserCheck className="w-3.5 h-3.5 text-indigo-600 shrink-0" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent align="end" className="rounded-xl shadow-lg border-slate-200/80">
                <SelectItem value="MoSPI Reviewer" className="text-xs cursor-pointer">MoSPI Reviewer (Admin)</SelectItem>
                <SelectItem value="District Authority Auditor" className="text-xs cursor-pointer">District Auditor</SelectItem>
                <SelectItem value="Read-Only Public Tier" className="text-xs cursor-pointer">Public Tier (Read-Only)</SelectItem>
              </SelectContent>
            </Select>

            {/* User logout button if logged in */}
            {loggedInUser && currentRole !== 'Read-Only Public Tier' && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onLogout}
                className="h-9 px-2.5 rounded-xl text-xs font-medium text-slate-500 hover:text-slate-800 hover:bg-slate-100 cursor-pointer"
              >
                Logout ({loggedInUser})
              </Button>
            )}
          </div>

          {/* Mobile Role Bar (< lg only) */}
          <div className="flex lg:hidden items-center justify-between gap-2 pt-1 border-t border-slate-200/60">
            <Select value={currentRole} onValueChange={handleRoleChange}>
              <SelectTrigger className="h-8 text-xs rounded-lg flex-1 border-slate-200/90 bg-white">
                <UserCheck className="w-3.5 h-3.5 text-indigo-600 shrink-0 mr-1" />
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="MoSPI Reviewer" className="text-xs">MoSPI Reviewer (Admin)</SelectItem>
                <SelectItem value="District Authority Auditor" className="text-xs">District Auditor</SelectItem>
                <SelectItem value="Read-Only Public Tier" className="text-xs">Public Tier (Read-Only)</SelectItem>
              </SelectContent>
            </Select>

            {loggedInUser && currentRole !== 'Read-Only Public Tier' && (
              <Button
                variant="ghost"
                size="sm"
                onClick={onLogout}
                className="h-8 px-2 rounded-lg text-xs font-medium text-slate-500"
              >
                Logout
              </Button>
            )}
          </div>

        </div>
      </header>
    </TooltipProvider>
  );
}
