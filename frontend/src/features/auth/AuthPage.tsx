import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Bus, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import { api } from "@/lib/apiClient";
import { useAuth, type Role } from "@/lib/auth";

function homeFor(role: Role | null): string {
  if (role === "admin") return "/";
  if (role === "driver") return "/driver";
  return "/parent";
}

export function AuthPage() {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { signIn } = useAuth();
  const { toast } = useToast();

  const [isLogin, setIsLogin] = useState(true);
  const [showForgot, setShowForgot] = useState(params.get("forgot") === "1");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [role, setRole] = useState<"driver" | "parent">("parent");
  const [pin, setPin] = useState("");
  const [loading, setLoading] = useState(false);

  const finishAuth = (
    token: string,
    user: { id: string; email: string; fullName?: string | null; role?: Role | null },
  ) => {
    const resolved: Role | null = user.role ?? null;
    signIn(token, {
      id: user.id,
      email: user.email,
      fullName: user.fullName ?? null,
      role: resolved,
    });
    navigate(homeFor(resolved));
  };

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await api.post("/api/auth/login", { email, password });
      finishAuth(res.token, res.user);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await api.post("/api/auth/signup", { email, password, fullName, role });
      toast({
        title: "Account created!",
        description: "Please check your email to verify your account.",
      });
      const res = await api.post("/api/auth/login", { email, password });
      finishAuth(res.token, res.user);
    } catch (err) {
      toast({ title: "Error", description: (err as Error).message, variant: "destructive" });
    } finally {
      setLoading(false);
    }
  };

  const handlePinLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      const res = await api.post("/api/auth/pin-login", { pin });
      finishAuth(res.token, res.user);
    } catch {
      toast({
        title: "Invalid PIN",
        description: "Please check your PIN and try again.",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  const handleForgot = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    try {
      await api.post("/api/auth/forgot-password", { email });
      toast({ title: "Reset link sent", description: "Check your email for a password reset link." });
      setShowForgot(false);
    } catch (err) {
      toast({
        title: "Error",
        description: (err as Error).message ?? "Unable to send reset link",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background flex items-center justify-center p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-2">
          <div className="inline-flex items-center justify-center h-14 w-14 rounded-2xl bg-primary text-primary-foreground">
            <Bus className="h-7 w-7" />
          </div>
          <h1 className="text-2xl font-bold font-heading text-foreground">SafeRide</h1>
          <p className="text-muted-foreground text-sm">School bus management platform</p>
        </div>

        <Card>
          <CardHeader className="text-center pb-2">
            <CardTitle className="font-heading">
              {showForgot ? "Reset Password" : isLogin ? "Welcome back" : "Create account"}
            </CardTitle>
            <CardDescription>
              {showForgot
                ? "Enter your email to receive a reset link"
                : isLogin
                  ? "Sign in to your account"
                  : "Sign up for SafeRide"}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {showForgot ? (
              <form onSubmit={handleForgot} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input id="email" type="email" placeholder="you@example.com" required value={email} onChange={(e) => setEmail(e.target.value)} />
                </div>
                <Button type="submit" className="w-full" disabled={loading}>
                  {loading ? "Sending…" : "Send Reset Link"}
                </Button>
                <div className="text-center">
                  <button type="button" className="text-primary text-sm font-medium hover:underline" onClick={() => setShowForgot(false)}>
                    Back to sign in
                  </button>
                </div>
              </form>
            ) : isLogin ? (
              <Tabs defaultValue="email">
                <TabsList className="grid w-full grid-cols-2 mb-4">
                  <TabsTrigger value="email">Email &amp; Password</TabsTrigger>
                  <TabsTrigger value="pin" className="gap-1.5">
                    <KeyRound className="h-3.5 w-3.5" /> Driver PIN
                  </TabsTrigger>
                </TabsList>
                <TabsContent value="email">
                  <form onSubmit={handleLogin} className="space-y-4">
                    <div className="space-y-2">
                      <Label htmlFor="email">Email</Label>
                      <Input id="email" type="email" placeholder="you@example.com" required value={email} onChange={(e) => setEmail(e.target.value)} />
                    </div>
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <Label htmlFor="password">Password</Label>
                        <button type="button" className="text-xs text-primary hover:underline" onClick={() => setShowForgot(true)}>
                          Forgot password?
                        </button>
                      </div>
                      <Input id="password" type="password" placeholder="••••••••" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} />
                    </div>
                    <Button type="submit" className="w-full" disabled={loading}>
                      {loading ? "Please wait..." : "Sign In"}
                    </Button>
                  </form>
                </TabsContent>
                <TabsContent value="pin">
                  <form onSubmit={handlePinLogin} className="space-y-4">
                    <div className="space-y-2">
                      <Label htmlFor="pin">Driver PIN</Label>
                      <Input
                        id="pin"
                        inputMode="numeric"
                        autoComplete="one-time-code"
                        placeholder="••••"
                        maxLength={6}
                        value={pin}
                        onChange={(e) => setPin(e.target.value.replace(/\D/g, ""))}
                      />
                    </div>
                    <Button type="submit" className="w-full" disabled={loading || pin.length < 4}>
                      {loading ? "Please wait..." : "Sign In with PIN"}
                    </Button>
                  </form>
                </TabsContent>
              </Tabs>
            ) : (
              <form onSubmit={handleSignup} className="space-y-4">
                <div className="space-y-2">
                  <Label htmlFor="fullName">Full name</Label>
                  <Input id="fullName" required value={fullName} onChange={(e) => setFullName(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="email">Email</Label>
                  <Input id="email" type="email" placeholder="you@example.com" required value={email} onChange={(e) => setEmail(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="password">Password</Label>
                  <Input id="password" type="password" placeholder="••••••••" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="role">I am a</Label>
                  <Select value={role} onValueChange={(v) => setRole(v as "driver" | "parent")}>
                    <SelectTrigger id="role">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="parent">Parent</SelectItem>
                      <SelectItem value="driver">Driver</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <Button type="submit" className="w-full" disabled={loading}>
                  {loading ? "Please wait..." : "Create Account"}
                </Button>
              </form>
            )}
          </CardContent>
        </Card>

        {!showForgot && (
          <p className="text-center text-sm text-muted-foreground">
            {isLogin ? "Don't have an account? " : "Already have an account? "}
            <button type="button" className="text-primary font-medium hover:underline" onClick={() => setIsLogin(!isLogin)}>
              {isLogin ? "Sign up" : "Sign in"}
            </button>
          </p>
        )}
      </div>
    </div>
  );
}
