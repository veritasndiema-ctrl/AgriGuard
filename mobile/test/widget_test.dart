import 'package:flutter_test/flutter_test.dart';
import 'package:agriguard_mobile/main.dart';

void main() {
  testWidgets('AgriGuard app smoke test', (WidgetTester tester) async {
    await tester.pumpWidget(const AgriGuardApp());
    // App should render the home shell without crashing
    expect(find.text('Forecast'), findsOneWidget);
  });
}
