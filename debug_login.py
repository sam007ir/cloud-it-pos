from app import create_app
import traceback

app = create_app()

with app.test_client() as client:
    with app.app_context():
        resp = client.post('/login', data={
            'username': 'admin',
            'password': 'admin123'
        }, follow_redirects=False)
        
        print('=== LOGIN RESPONSE ===')
        print('Status:', resp.status_code)
        print('Redirect Location:', resp.headers.get('Location'))
        
        print('\n=== ACCESSING DASHBOARD ===')
        try:
            resp2 = client.get('/dashboard', follow_redirects=True)
            print('Dashboard status:', resp2.status_code)
            if resp2.status_code >= 400:
                content = resp2.get_data(as_text=True)
                print('Error content (first 1000 chars):')
                print(content[:1000])
        except Exception as e:
            print('Exception occurred:')
            traceback.print_exc()
