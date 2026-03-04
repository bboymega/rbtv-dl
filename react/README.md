# Creating a static build of the Web UI

Change into the React application directory
```
cd react
```

Configure the environment variables
```
nano env.local.example
```

Set the backend base URL by editing the following variable
```
NEXT_PUBLIC_SITE_URL=<flask-backend-base-url>
```

Copy the example file to create the local environment configuration, then run the build command
```
cp env.local.example .env.local
npm run build
```

The generated static files will be available in the `out` directory.

You can deploy the contents of this directory to your own hosting service or any static web server.
