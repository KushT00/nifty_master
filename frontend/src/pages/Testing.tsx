import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Activity, CheckCircle2 } from 'lucide-react'

export default function TestingPage() {
  return (
    <div className="p-8 space-y-8">
      <Card className="border-primary/20 bg-primary/5">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-5 w-5 text-primary" />
            Frontend Connectivity Test
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-4 p-4 bg-background rounded-xl border">
            <CheckCircle2 className="h-6 w-6 text-green-500" />
            <div>
              <p className="font-bold text-lg">React Mount Successful</p>
              <p className="text-sm text-muted-foreground">If you can see this, the routing and component rendering are working correctly.</p>
            </div>
          </div>
          
          <div className="mt-6 grid grid-cols-2 gap-4">
            <Badge variant="outline" className="justify-center py-2">Environment: Production</Badge>
            <Badge variant="secondary" className="justify-center py-2">Path: /testing</Badge>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
